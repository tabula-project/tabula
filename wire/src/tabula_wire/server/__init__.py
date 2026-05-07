"""Tabula server-side wire layer.

This package owns the server-side of the Noise-framed chat channel. Public
surface, organized by sub-issue:

Listener (issue #20, landed in #55):

- :func:`serve` — start an asyncio TCP listener; accepts connections,
  runs Noise XX responder handshake, then spins a framed read/write
  loop and hands the session to a user-supplied async callback.
- :class:`HandshakeInfo` — per-connection summary returned to the
  session callback (peer address, pinned remote static pubkey).
- :class:`Session` — the (recv_iter, send_fn) pair handed to the
  callback.
- :data:`SessionCallback` — type alias for the per-session callback.

Session manager (issue #25, landed in #49):

- :func:`handle_session` — drives one Noise session: Hello/Welcome,
  per-turn streaming of assistant tokens, EndSession, error mapping.
- :func:`handle_connection` — listener ``on_session`` callback that
  combines a capacity semaphore with :func:`handle_session`.
- :func:`serve_with_sessions` — high-level entrypoint that wires the
  listener (``serve``) to the session manager via
  :func:`handle_connection`.
- :func:`reject_at_capacity` — send a typed ``ErrorFrame{AT_CAPACITY}``.
- :class:`ClaudeProcessProtocol` / :data:`ClaudeFactory` — Protocols
  decoupling the session manager from the claude driver.
- :data:`SendFrame` — type alias for the per-session send callable.
- :class:`ServerConfig` — server configuration knobs.

Claude driver (issue #22, consolidated under this package in #70):

- :class:`ClaudeProcess` — async subprocess driver around the
  ``claude`` CLI. One subprocess per session (per Epic #13).
- :class:`ClaudeProcessCrashed` — raised by ``stream_tokens`` when
  the subprocess exits unexpectedly.
- :class:`ClaudeProcessNotStarted` — raised when an operation requires
  the process to have been started (e.g. ``send_prompt`` before
  ``start``).

This package does NOT:

- implement the client side (issue #27)
- enforce server-pubkey pinning (issue #32, on the *client* side)
"""

from .claude_driver import (
    ClaudeProcess,
    ClaudeProcessCrashed,
    ClaudeProcessNotStarted,
)
from .config import ServerConfig
from .listener import (
    HandshakeInfo,
    Session,
    SessionCallback,
    serve,
)
from .main import (
    handle_connection,
    serve_with_sessions,
)
from .session import (
    ClaudeFactory,
    ClaudeProcessProtocol,
    SendFrame,
    handle_session,
    reject_at_capacity,
)

__all__ = [
    # listener (#20 / #55)
    "HandshakeInfo",
    "Session",
    "SessionCallback",
    "serve",
    # session manager (#25)
    "ClaudeFactory",
    "ClaudeProcessProtocol",
    "SendFrame",
    "ServerConfig",
    "handle_connection",
    "handle_session",
    "reject_at_capacity",
    "serve_with_sessions",
    # claude driver (#22, consolidated in #70)
    "ClaudeProcess",
    "ClaudeProcessCrashed",
    "ClaudeProcessNotStarted",
]
