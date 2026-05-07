"""Tabula server-side wire layer.

Public surface:

- :func:`serve` — start an asyncio TCP listener; accepts connections,
  runs Noise XX responder handshake, then spins a framed read/write
  loop and hands the session to a user-supplied async callback.

- :class:`HandshakeInfo` — per-connection summary returned to the
  session callback (peer address, pinned remote static pubkey).

- :class:`Session` — the (recv_iter, send_fn) pair handed to the
  callback.

This package does NOT:

- spawn or manage the claude subprocess (issue #22)
- implement session lifecycle / Hello / Welcome semantics (issue #25)
- implement the client side (issue #27)
- enforce server-pubkey pinning (issue #32, on the *client* side)
"""

from .listener import (
    HandshakeInfo,
    Session,
    SessionCallback,
    serve,
)

__all__ = [
    "HandshakeInfo",
    "Session",
    "SessionCallback",
    "serve",
]
