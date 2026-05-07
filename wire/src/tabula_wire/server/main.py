"""Server entrypoint.

This module is intentionally thin: it composes the listener (#20, landed in
#55), the claude driver factory (#22), and the session manager (#25, this
issue) into a runnable server with a single capacity semaphore.

The session manager *uses* the listener's :func:`tabula_wire.server.listener.serve`
rather than duplicating it — this module exposes :func:`serve_with_sessions`,
the high-level entrypoint that wires the two together via
:func:`handle_connection`.

The capacity check uses :func:`asyncio.Semaphore.locked` plus a non-blocking
``acquire`` so that an over-capacity client gets a typed ``ErrorFrame`` and
a clean disconnect rather than waiting indefinitely.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from tabula_wire.proto.v1 import ClientFrame
from tabula_wire.server.config import ServerConfig
from tabula_wire.server.listener import (
    HandshakeInfo,
    Session,
    serve as listener_serve,
)
from tabula_wire.server.session import (
    ClaudeFactory,
    SendFrame,
    handle_session,
    reject_at_capacity,
)

_logger = logging.getLogger("tabula_wire.server.main")


async def handle_connection(
    handshake_info: Any,
    recv_frames: AsyncIterator[ClientFrame],
    send_frame: SendFrame,
    *,
    config: ServerConfig,
    claude_factory: ClaudeFactory,
    semaphore: asyncio.Semaphore,
) -> None:
    """Listener ``on_session`` callback.

    Acquires a slot from the global capacity semaphore *without blocking*.
    If no slot is available, sends ``ErrorFrame{AT_CAPACITY}`` and returns;
    the listener then closes the transport. Otherwise dispatches to
    :func:`tabula_wire.server.session.handle_session`.
    """
    # Try a non-blocking acquire. ``asyncio.Semaphore`` does not expose
    # ``acquire_nowait`` directly, so we check ``locked()`` (which returns
    # True iff the internal counter is 0) before awaiting acquire. There is
    # a benign race: two callers could both pass the check and one then
    # blocks on acquire. We accept that — capacity is a soft cap and the
    # blocked caller will simply wait one slot, which is preferable to
    # rejecting under-capacity clients.
    if semaphore.locked():
        _logger.info("rejecting connection: at capacity")
        await reject_at_capacity(send_frame)
        return

    async with semaphore:
        await handle_session(
            handshake_info,
            recv_frames,
            send_frame,
            config=config,
            claude_factory=claude_factory,
        )


async def serve_with_sessions(
    host: str,
    port: int,
    static_key: bytes,
    claude_factory: ClaudeFactory,
    *,
    config: ServerConfig | None = None,
) -> asyncio.base_events.Server:
    """Run the chat server: listener + session manager wired together.

    This is the canonical entrypoint that combines:

    - :func:`tabula_wire.server.listener.serve` (TCP + Noise XX + framed I/O)
    - :func:`handle_connection` (capacity semaphore)
    - :func:`tabula_wire.server.session.handle_session` (Hello/Welcome,
      per-turn streaming, error mapping)

    Renamed from ``serve`` to avoid a name collision with
    :func:`tabula_wire.server.listener.serve`. The session manager *uses*
    the listener's ``serve``; it does not duplicate it.

    Args:
        host: bind address (e.g. ``"0.0.0.0"`` or ``"127.0.0.1"``).
        port: bind port.
        static_key: server's 32-byte X25519 static private key.
        claude_factory: async factory producing fresh ``ClaudeProcess``
            instances per session.
        config: server configuration. Defaults to ``ServerConfig.from_env()``.

    Returns:
        The ``asyncio.Server`` from the underlying listener. Call
        ``server.close()`` and ``await server.wait_closed()`` to stop.

    Note:
        This function returns as soon as the listener is bound; it
        does NOT call ``serve_forever``. The caller controls the
        accept loop's lifetime via the returned ``Server``.
    """
    cfg = config or ServerConfig.from_env()
    sem = asyncio.Semaphore(cfg.max_concurrent_sessions)

    async def _on_session(handshake_info: HandshakeInfo, session: Session) -> None:
        await handle_connection(
            handshake_info,
            session.recv_frames,
            session.send_frame,
            config=cfg,
            claude_factory=claude_factory,
            semaphore=sem,
        )

    return await listener_serve(host, port, static_key, _on_session)


__all__ = ["handle_connection", "serve_with_sessions"]
