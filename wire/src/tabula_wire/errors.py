"""Typed exception hierarchy for the Tabula wire library.

This module is the single source of truth for cross-cutting wire error
semantics described in issue #36. It defines:

1. The typed exception hierarchy used internally by the server's session
   loop (``wire/server/session.py``) and the client's frame loop
   (``wire/client/session.py``) to communicate failures between layers.
2. The mapping table from server-side exception type to
   ``ErrorFrame.Code``. The server catches a ``WireError`` (or
   ``SubprocessError``) at the top of the per-session task, translates it
   via :func:`exception_to_error_code`, sends a best-effort ``ErrorFrame``,
   and then closes the Noise stream.

Layering (from #36):

* ``WireError`` and its subclasses live in this library; they are
  *internal* representations of failure.
* ``ErrorFrame`` (defined in proto, see ``tabula_wire.proto.v1``) is the
  *wire* representation. The server emits it; the client receives it,
  re-raises it as a typed exception, and the CLI (#29) renders it.

The exception hierarchy:

    WireError (base)
    ├── HandshakeError
    │   ├── HandshakeTimeout
    │   ├── ServerKeyMismatch
    │   └── MalformedHandshake
    ├── SessionError
    │   ├── ServerDisconnected
    │   ├── ClientDisconnected
    │   ├── OversizeFrame
    │   └── MalformedFrame
    ├── SubprocessError
    │   ├── ClaudeCrashed
    │   └── ClaudeTimeout
    ├── AtCapacity
    └── InternalError

Each leaf carries enough structured fields to render an actionable message
without inspecting tracebacks.
"""

from __future__ import annotations

from typing import Optional, Type

from tabula_wire.proto.v1 import ErrorFrame


# ---------------------------------------------------------------------------
# Base hierarchy
# ---------------------------------------------------------------------------


class WireError(Exception):
    """Base class for all errors crossing the Tabula wire boundary.

    Subclasses MUST be importable without a TCP/Noise/proto stack — they
    are the *vocabulary* both peers use to talk about failure, regardless
    of which side noticed first.
    """


class HandshakeError(WireError):
    """The Noise XX handshake failed to establish a session.

    Raised on either peer before a session is live. Recovery is impossible
    at this layer — the caller's job is to log and exit non-zero (client)
    or close the connection and keep listening (server).
    """


class SessionError(WireError):
    """An established session terminated abnormally.

    Raised after a successful handshake, while frames are flowing.
    """


class SubprocessError(WireError):
    """The server-side ``claude`` subprocess misbehaved.

    Raised only on the server. The session loop catches this, translates
    to ``ErrorFrame``, and tears down the per-session task. The kill
    mechanics live in #22 — this exception is the signal *to* invoke them,
    not the implementation of them.
    """


# ---------------------------------------------------------------------------
# HandshakeError leaves
# ---------------------------------------------------------------------------


class HandshakeTimeout(HandshakeError):
    """Peer did not complete the XX handshake within the configured deadline.

    Raised on both sides. Default deadline is 10s (per #36 acceptance
    criteria); configurable per peer.
    """

    def __init__(self, *, timeout_s: float, peer: str = "") -> None:
        self.timeout_s = float(timeout_s)
        self.peer = peer
        msg = f"Noise XX handshake timed out after {timeout_s:.1f}s"
        if peer:
            msg += f" with peer {peer}"
        super().__init__(msg)


class ServerKeyMismatch(HandshakeError):
    """Server's static Noise key did not match the client's pin.

    Raised on the *client* side only. The pin store lives at
    ``~/.config/tabula/known_servers`` (see #32). The CLI surfaces both
    keys in hex so the operator can manually reconcile.
    """

    def __init__(self, *, expected_key: bytes, actual_key: bytes, server: str = "") -> None:
        self.expected_key = bytes(expected_key)
        self.actual_key = bytes(actual_key)
        self.server = server
        msg = (
            "Server static key did not match the pinned key for "
            f"{server or 'this server'}.\n"
            f"  expected (hex): {self.expected_key.hex()}\n"
            f"  actual   (hex): {self.actual_key.hex()}"
        )
        super().__init__(msg)


class MalformedHandshake(HandshakeError):
    """Received bytes were not a valid XX handshake message.

    Common cases: protocol-magic mismatch, garbage on the wire, a peer
    speaking a different protocol entirely. The server treats this as a
    log-and-drop event; the listener keeps accepting new connections.
    """

    def __init__(self, *, reason: str, bytes_seen: int = 0, peer: str = "") -> None:
        self.reason = reason
        self.bytes_seen = int(bytes_seen)
        self.peer = peer
        msg = f"Malformed handshake: {reason}"
        if bytes_seen:
            msg += f" (after {bytes_seen} bytes)"
        if peer:
            msg += f" from {peer}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# SessionError leaves
# ---------------------------------------------------------------------------


class ServerDisconnected(SessionError):
    """Server closed the TCP connection while the client was reading.

    Raised on the *client* side. The CLI (#29) renders this as a
    user-facing "server went away" message and exits non-zero.
    """

    def __init__(self, *, during: str = "frame_loop") -> None:
        self.during = during
        super().__init__(f"Server disconnected during {during}")


class ClientDisconnected(SessionError):
    """Client closed the TCP connection while the server was reading/writing.

    Raised on the *server* side. The session loop's job on receipt is to
    cancel any in-flight subprocess turn (per #22 kill semantics) and
    remove the session row from the manager (#25).
    """

    def __init__(self, *, during: str = "frame_loop", session_id: str = "") -> None:
        self.during = during
        self.session_id = session_id
        msg = f"Client disconnected during {during}"
        if session_id:
            msg += f" (session {session_id})"
        super().__init__(msg)


class OversizeFrame(SessionError):
    """A length-prefix advertised more bytes than ``MAX_FRAME_SIZE`` allows.

    Both peers raise this. The receiver MUST close the connection without
    attempting to read the body — failing to do so opens a memory-amplification
    DoS path. Default cap is 1 MiB (per #36 acceptance criteria).
    """

    def __init__(self, *, advertised: int, limit: int) -> None:
        self.advertised = int(advertised)
        self.limit = int(limit)
        super().__init__(
            f"Frame length {advertised} exceeds maximum {limit}; closing connection"
        )


class MalformedFrame(SessionError):
    """A frame's bytes decrypted but failed protobuf parsing.

    Decryption succeeded (so the peer holds the right Noise key), but the
    resulting bytes were not a valid ``ClientFrame`` / ``ServerFrame``.
    The server attempts a best-effort ``ErrorFrame{code=PROTOCOL}`` send
    before close.
    """

    def __init__(self, *, reason: str, frame_kind: str = "") -> None:
        self.reason = reason
        self.frame_kind = frame_kind
        msg = "Malformed frame"
        if frame_kind:
            msg += f" ({frame_kind})"
        msg += f": {reason}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# SubprocessError leaves
# ---------------------------------------------------------------------------


class ClaudeCrashed(SubprocessError):
    """The ``claude`` CLI subprocess exited non-zero mid-turn.

    The session loop catches this and emits ``ErrorFrame{code=CLAUDE_CRASHED}``
    before closing the session.
    """

    def __init__(self, *, returncode: int, stderr_tail: str = "") -> None:
        self.returncode = int(returncode)
        self.stderr_tail = stderr_tail
        msg = f"claude subprocess exited with code {returncode}"
        if stderr_tail:
            # Keep tail bounded; never log secrets.
            tail = stderr_tail.strip().splitlines()[-1:]
            if tail:
                msg += f": {tail[0]}"
        super().__init__(msg)


class ClaudeTimeout(SubprocessError):
    """The ``claude`` CLI subprocess produced no stdout for too long.

    The hang heuristic is "no stdout byte for ``idle_timeout_s`` seconds",
    NOT total elapsed time (per #36 implementation notes). Default 60s,
    configurable via ``TABULA_CLAUDE_IDLE_TIMEOUT_S``.

    The session loop catches this, kills the subprocess (per #22), and
    emits ``ErrorFrame{code=CLAUDE_TIMEOUT}``.
    """

    def __init__(self, *, idle_timeout_s: float) -> None:
        self.idle_timeout_s = float(idle_timeout_s)
        super().__init__(
            f"claude subprocess produced no output for {idle_timeout_s:.1f}s; "
            "treating as hung"
        )


# ---------------------------------------------------------------------------
# Cross-cutting leaves
# ---------------------------------------------------------------------------


class AtCapacity(WireError):
    """Server is at its session-count limit; new session refused.

    Raised on the *server* before a session is admitted (post-handshake,
    pre-Welcome). Translates to ``ErrorFrame{code=AT_CAPACITY}``. The
    capacity semaphore lives in #25.
    """

    def __init__(self, *, current: int, limit: int) -> None:
        self.current = int(current)
        self.limit = int(limit)
        super().__init__(
            f"Server at capacity: {current}/{limit} sessions in use; refusing new session"
        )


class InternalError(WireError):
    """Catch-all for unexpected server-side bugs.

    The top of the session task wraps any non-``WireError`` exception in
    this class (preserving the cause via ``__cause__``) so the wire-level
    behavior is uniform: log, translate to ``ErrorFrame{code=INTERNAL}``,
    close. Untyped exceptions never escape to the listener loop.
    """

    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# Mapping table — server-side exception → ErrorFrame.Code
# ---------------------------------------------------------------------------


# Resolution order is important: more specific types first. The table is
# evaluated as "most-specific match wins", which is achieved by walking the
# input exception's MRO and picking the first key that is a superclass of
# the input. Subclasses inherit the mapping of their nearest mapped ancestor.
_EXCEPTION_TO_CODE: dict[Type[BaseException], ErrorFrame.Code] = {
    # SubprocessError leaves
    ClaudeCrashed: ErrorFrame.Code.CLAUDE_CRASHED,
    ClaudeTimeout: ErrorFrame.Code.CLAUDE_TIMEOUT,
    # SessionError leaves that map to PROTOCOL
    OversizeFrame: ErrorFrame.Code.PROTOCOL,
    MalformedFrame: ErrorFrame.Code.PROTOCOL,
    # HandshakeError leaves that map to PROTOCOL
    MalformedHandshake: ErrorFrame.Code.PROTOCOL,
    # Capacity
    AtCapacity: ErrorFrame.Code.AT_CAPACITY,
    # Family-level fallbacks (catch-alls):
    HandshakeError: ErrorFrame.Code.PROTOCOL,
    SubprocessError: ErrorFrame.Code.CLAUDE_CRASHED,
    SessionError: ErrorFrame.Code.PROTOCOL,
    # Last-resort fallback. Any non-WireError exception caught at the top
    # of the session task is wrapped in InternalError before reaching here.
    InternalError: ErrorFrame.Code.INTERNAL,
    WireError: ErrorFrame.Code.INTERNAL,
}


def exception_to_error_code(exc: BaseException) -> ErrorFrame.Code:
    """Translate a server-side exception to an ``ErrorFrame.Code``.

    Used at the top of the per-session task in ``wire/server/session.py``:
    catch a ``WireError`` (or ``SubprocessError``), map it here, send a
    best-effort ``ErrorFrame`` with this code and ``str(exc)`` as message,
    then close the Noise stream.

    Resolution rule: walk ``type(exc).__mro__`` and pick the first entry
    that appears in the mapping table. This guarantees that more specific
    subclasses override family-level defaults regardless of dictionary
    insertion order.

    Unknown exception types fall back to ``INTERNAL``. Callers MUST log
    the original exception when this happens; the wire ``ErrorFrame`` text
    intentionally does not echo arbitrary internals back to the client.
    """
    for klass in type(exc).__mro__:
        if klass in _EXCEPTION_TO_CODE:
            return _EXCEPTION_TO_CODE[klass]
    return ErrorFrame.Code.INTERNAL


# ---------------------------------------------------------------------------
# Helper for building best-effort ErrorFrames
# ---------------------------------------------------------------------------


def build_error_frame(
    exc: BaseException, *, turn_id: Optional[str] = None
) -> ErrorFrame:
    """Build an ``ErrorFrame`` from a server-side exception.

    Convenience for the server's session loop. Centralizes the
    "exception → wire error" translation so both the cleanup path and
    the test suite share one implementation.

    The message uses ``str(exc)``. Subclasses defined above carry only
    structured fields that are safe to surface (peer addr, hex-encoded
    public keys, byte counts, return codes). Internal stack frames and
    arbitrary Python repr never leak.
    """
    code = exception_to_error_code(exc)
    return ErrorFrame(
        code=code,
        message=str(exc),
        turn_id=turn_id or "",
    )


__all__ = [
    # Base
    "WireError",
    # Handshake family
    "HandshakeError",
    "HandshakeTimeout",
    "ServerKeyMismatch",
    "MalformedHandshake",
    # Session family
    "SessionError",
    "ServerDisconnected",
    "ClientDisconnected",
    "OversizeFrame",
    "MalformedFrame",
    # Subprocess family
    "SubprocessError",
    "ClaudeCrashed",
    "ClaudeTimeout",
    # Cross-cutting
    "AtCapacity",
    "InternalError",
    # Mapping helpers
    "exception_to_error_code",
    "build_error_frame",
]
