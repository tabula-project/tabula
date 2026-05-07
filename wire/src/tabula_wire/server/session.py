"""Session manager: middle layer between transport and claude subprocess.

The listener (issue #20) accepts a Noise-authenticated TCP connection and,
after a successful handshake, calls back into :func:`handle_session` with:

- ``handshake_info``: opaque metadata about the handshake (e.g. peer pubkey).
  This module currently treats it as opaque; it is logged but does not
  affect routing because the MVP is single-user / single-tenant.
- ``recv_frames``: an async iterator yielding decrypted :class:`ClientFrame`
  values, raising :class:`asyncio.IncompleteReadError`/EOF semantics on
  transport close.
- ``send_frame``: an async callable that serializes, encrypts, and writes
  one :class:`ServerFrame`.

For each accepted session this module:

1. Reads exactly one frame and asserts it is a :class:`Hello` whose
   ``protocol_version`` matches the server. Anything else → ``ErrorFrame``
   with code ``PROTOCOL`` and the function returns.
2. Generates a UUID4 session id, spawns a ``ClaudeProcess`` (per the
   :class:`ClaudeProcessProtocol` contract), and replies with
   :class:`Welcome`.
3. For each subsequent :class:`UserMessage`, drives the subprocess and
   streams :class:`AssistantToken` frames (sequence reset per turn) capped
   by an :class:`AssistantTurnEnd`.
4. On :class:`EndSession` returns cleanly. On transport disconnect, returns
   cleanly. On subprocess crash, sends ``ErrorFrame{CLAUDE_CRASHED}`` and
   returns.
5. *Always* kills the subprocess in a ``finally`` block before returning.

Capacity (max concurrent sessions) is enforced by the listener entrypoint
through an :class:`asyncio.Semaphore`. When the semaphore cannot be acquired
without blocking, the listener should call :func:`reject_at_capacity` after
reading the client's :class:`Hello` to give the client a typed
``ErrorFrame{AT_CAPACITY}`` and then close the transport. See
:func:`tabula_wire.server.main.handle_connection` for the canonical wiring.

Per the epic's locked decisions:

- One Noise session ↔ one ``claude`` subprocess.
- No session reuse.
- Single-user, single-session — no multi-tenant routing.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from tabula_wire.proto.v1 import (
    AssistantToken,
    AssistantTurnEnd,
    ClientFrame,
    EndSession,
    ErrorCode,
    ErrorFrame,
    FinishReason,
    Hello,
    ServerFrame,
    UserMessage,
    Welcome,
)
from tabula_wire.server.config import ServerConfig


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ClaudeProcessCrashed(RuntimeError):
    """Raised by ``ClaudeProcess.stream_tokens`` when the subprocess died.

    This module re-exports the exception type that the driver in #22 will
    raise. Defining it here keeps the session manager landable before #22
    merges; the driver should import this symbol (or a compatible class
    with the same name in :mod:`tabula_wire.server.claude_driver`).

    Attributes:
        exit_code: subprocess exit status, if known.
        stderr_tail: last ~1 KiB of stderr, if captured.
    """

    def __init__(
        self,
        message: str = "claude subprocess crashed",
        *,
        exit_code: int | None = None,
        stderr_tail: str = "",
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail


@runtime_checkable
class ClaudeProcessProtocol(Protocol):
    """Minimum surface the session manager requires of a claude driver.

    The concrete implementation lives in :mod:`tabula_wire.server.claude_driver`
    (issue #22). Tests inject a fake instead.
    """

    @property
    def is_alive(self) -> bool: ...

    async def send_prompt(self, text: str) -> None:
        """Forward one user prompt to the subprocess's stdin."""
        ...

    def stream_tokens(self) -> AsyncIterator[str]:
        """Yield stdout chunks for the *current* turn until turn end.

        Must raise :class:`ClaudeProcessCrashed` if the subprocess exits
        unexpectedly mid-stream. Must terminate cleanly (StopAsyncIteration)
        when the model signals a turn boundary.

        Note: declared as an ordinary method (not ``async def``) because
        async generators are themselves callables returning iterators; this
        keeps the Protocol compatible with both ``async def`` and explicit
        iterator implementations.
        """
        ...

    async def kill(self) -> int | None:
        """Idempotently terminate the subprocess and return its exit code.

        Safe to call multiple times. Returns ``None`` if the process never
        started; otherwise the exit code (which may be a signal-derived
        negative value on POSIX).
        """
        ...


# Type alias for the ``send_frame`` callback supplied by the listener.
SendFrame = Callable[[ServerFrame], Awaitable[None]]

# Factory injected by the listener entrypoint so each session can spawn a
# fresh subprocess. Returns an *unstarted-or-started* ``ClaudeProcessProtocol``
# instance; the contract is that the returned object is ready to receive
# ``send_prompt`` / ``stream_tokens`` calls.
ClaudeFactory = Callable[[ServerConfig], Awaitable[ClaudeProcessProtocol]]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


_logger = logging.getLogger("tabula_wire.server.session")


class _SessionLogAdapter(logging.LoggerAdapter):
    """Prefixes every log line with the session id for greppability.

    We intentionally never log prompt text or assistant token text — the
    transport is end-to-end encrypted and content is private to the user.
    """

    def process(
        self, msg: str, kwargs: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        sid = self.extra.get("session_id", "?") if self.extra else "?"
        return f"[session {sid}] {msg}", kwargs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send_error(
    send_frame: SendFrame,
    code: ErrorCode,
    message: str,
    log: logging.LoggerAdapter | logging.Logger,
) -> None:
    """Send an ``ErrorFrame`` swallowing transport errors.

    Once we are tearing a session down, a failure to deliver the error frame
    is itself unrecoverable — log it and move on. We never want a broken
    transport to mask a more interesting upstream cause of failure.
    """
    try:
        await send_frame(ServerFrame(error=ErrorFrame(code=code, message=message)))
    except Exception:  # noqa: BLE001 - intentional best-effort send
        log.warning(
            "failed to deliver ErrorFrame(code=%s) to client; transport likely closed",
            code.value,
        )


async def reject_at_capacity(send_frame: SendFrame) -> None:
    """Send an ``AT_CAPACITY`` error.

    The listener uses this when the global semaphore is exhausted. Per the
    issue spec the rejection happens after reading ``Hello`` so the client
    knows it spoke to a real Tabula server before being turned away.
    """
    await _send_error(
        send_frame,
        ErrorCode.AT_CAPACITY,
        "server is at capacity; try again later",
        _logger,
    )


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


async def _read_one_frame(
    recv_frames: AsyncIterator[ClientFrame],
) -> ClientFrame | None:
    """Pull the next frame, returning ``None`` on graceful EOF."""
    try:
        return await recv_frames.__anext__()
    except StopAsyncIteration:
        return None


async def _stream_one_turn(
    claude: ClaudeProcessProtocol,
    user_text: str,
    send_frame: SendFrame,
    log: logging.LoggerAdapter,
) -> FinishReason:
    """Drive one (UserMessage → token stream → TurnEnd) cycle.

    Returns the finish reason that was sent to the client. Raises only if
    the *send* side of the transport fails; subprocess crashes are caught
    and surfaced as an :class:`ErrorFrame` to the caller's caller.
    """
    await claude.send_prompt(user_text)

    sequence = 0
    try:
        async for chunk in claude.stream_tokens():
            await send_frame(
                ServerFrame(
                    assistant_token=AssistantToken(text=chunk, sequence=sequence)
                )
            )
            sequence += 1
    except ClaudeProcessCrashed as exc:
        log.error(
            "claude subprocess crashed mid-turn after %d token(s) "
            "(exit_code=%s)",
            sequence,
            exc.exit_code,
        )
        await _send_error(
            send_frame,
            ErrorCode.CLAUDE_CRASHED,
            str(exc) or "claude subprocess crashed",
            log,
        )
        return FinishReason.ERROR

    await send_frame(
        ServerFrame(assistant_turn_end=AssistantTurnEnd(finish_reason=FinishReason.STOP))
    )
    log.info("turn complete (%d token(s))", sequence)
    return FinishReason.STOP


async def handle_session(
    handshake_info: Any,
    recv_frames: AsyncIterator[ClientFrame],
    send_frame: SendFrame,
    *,
    config: ServerConfig,
    claude_factory: ClaudeFactory,
) -> None:
    """Drive one Noise-authenticated chat session to completion.

    See module docstring for the full state machine. This coroutine returns
    when the session has been fully torn down — either gracefully (client
    sent ``EndSession`` or hit EOF) or due to an error (protocol violation,
    subprocess crash, cancellation). The subprocess is *always* killed in
    a ``finally`` block, so any path out of this function is leak-free.

    Args:
        handshake_info: opaque handshake metadata from the listener; logged
            for diagnostics but not used for authorization in MVP.
        recv_frames: async iterator yielding decoded :class:`ClientFrame`s.
        send_frame: async callback to deliver one :class:`ServerFrame`.
        config: server configuration (cwd, protocol version, etc.).
        claude_factory: async factory returning a ready-to-use
            :class:`ClaudeProcessProtocol` instance.
    """
    session_id = str(uuid.uuid4())
    log = _SessionLogAdapter(_logger, {"session_id": session_id})
    log.info("accepted (handshake_info=%r)", handshake_info)

    claude: ClaudeProcessProtocol | None = None
    try:
        # ---- Hello / Welcome handshake ----
        first = await _read_one_frame(recv_frames)
        if first is None:
            log.info("client disconnected before sending Hello")
            return

        payload = first.payload()
        if not isinstance(payload, Hello):
            log.warning(
                "first frame was %s, expected Hello; closing",
                type(payload).__name__,
            )
            await _send_error(
                send_frame,
                ErrorCode.PROTOCOL,
                "first frame must be Hello",
                log,
            )
            return

        if payload.protocol_version != config.protocol_version:
            log.warning(
                "protocol version mismatch: client=%d server=%d",
                payload.protocol_version,
                config.protocol_version,
            )
            await _send_error(
                send_frame,
                ErrorCode.PROTOCOL,
                f"unsupported protocol_version {payload.protocol_version}; "
                f"server requires {config.protocol_version}",
                log,
            )
            return

        # ---- Spawn subprocess and welcome the client ----
        claude = await claude_factory(config)
        await send_frame(
            ServerFrame(
                welcome=Welcome(
                    session_id=session_id,
                    server_version=config.server_version,
                )
            )
        )
        log.info(
            "welcomed client (cwd=%s, server_version=%s)",
            config.cwd,
            config.server_version,
        )

        # ---- Main loop: UserMessage / EndSession until done ----
        while True:
            frame = await _read_one_frame(recv_frames)
            if frame is None:
                log.info("transport EOF; tearing down")
                return

            payload = frame.payload()
            if isinstance(payload, EndSession):
                log.info("client requested EndSession")
                return

            if isinstance(payload, UserMessage):
                finish = await _stream_one_turn(
                    claude, payload.text, send_frame, log
                )
                if finish == FinishReason.ERROR:
                    # Subprocess crashed; tear down rather than try to
                    # recover. The client has already been notified.
                    return
                continue

            # Hello after the initial handshake, or some unknown variant.
            log.warning(
                "unexpected mid-session frame %s; closing",
                type(payload).__name__,
            )
            await _send_error(
                send_frame,
                ErrorCode.PROTOCOL,
                f"unexpected mid-session frame {type(payload).__name__}",
                log,
            )
            return

    except asyncio.CancelledError:
        # The listener (or test) cancelled this task — propagate after the
        # finally block runs so the subprocess gets killed.
        log.info("session cancelled")
        raise
    except Exception:
        log.exception("unexpected error in session loop")
        # Best-effort notify the client; the transport may already be gone.
        await _send_error(
            send_frame,
            ErrorCode.INTERNAL,
            "internal server error",
            log,
        )
        # Do not re-raise: returning normally lets the listener clean up
        # the connection without it looking like a crash.
    finally:
        if claude is not None:
            try:
                exit_code = await claude.kill()
                log.info("subprocess killed (exit_code=%s)", exit_code)
            except Exception:  # noqa: BLE001 - cleanup must not raise
                log.exception("error while killing claude subprocess")
        log.info("session closed")


__all__ = [
    "ClaudeFactory",
    "ClaudeProcessCrashed",
    "ClaudeProcessProtocol",
    "SendFrame",
    "handle_session",
    "reject_at_capacity",
]
