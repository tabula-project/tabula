"""Client dialer stub (#27).

Defines the `ChatChannel` interface and a placeholder `connect` coroutine.
The real implementation (Noise XX, framing, pinning verification) lands in
issue #27. Until then the chat UI tests inject a fake `ChatChannel` rather
than calling `connect` directly, so this module only needs to define the
contract.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from wire.proto import ClientFrame, ServerFrame


@runtime_checkable
class ChatChannel(Protocol):
    """Bidirectional encrypted stream of typed frames.

    Lifecycle: caller `await connect(...)` -> uses `send`/`recv` -> calls
    `close()` (idempotent). `recv()` raises `ServerDisconnected` if the peer
    closes the stream without sending an explicit `EndSession` ack path.
    """

    async def send(self, frame: ClientFrame) -> None: ...

    async def recv(self) -> ServerFrame: ...

    async def close(self) -> None: ...


async def connect(
    host: str,
    port: int,
    local_static_key: bytes,
    expected_remote_static_pubkey: bytes,
    *,
    timeout: float = 10.0,
) -> ChatChannel:
    """Open a connection to a Tabula chat server.

    Stub: real implementation lands in #27. Raises
    :class:`NotImplementedError` so callers cannot accidentally rely on
    this stub for transport — the chat UI tests inject a fake channel.
    """

    raise NotImplementedError(
        "wire.client.dialer.connect is a stub; real implementation lands in #27"
    )


__all__ = ["ChatChannel", "connect"]
