"""Client-side wire surface (stub for #27, #32).

The chat UI in #29 only needs:
    - a `connect(...)` coroutine that returns a `ChatChannel`
    - a `ChatChannel` with `send`, `recv`, `close`
    - a typed exception hierarchy so the UI can map errors to exit codes
    - a pinning lookup (label -> host/port/expected_pubkey)

The real dialer (#27) and pinning store (#32) will replace these stubs.
The shapes below are deliberately the smallest contract the UI depends on.
"""

from wire.client.dialer import ChatChannel, connect
from wire.client.exceptions import (
    ClientError,
    ConnectError,
    ConnectRefused,
    ConnectTimeout,
    DnsError,
    ProtocolError,
    ServerDisconnected,
    ServerKeyMismatch,
)
from wire.client.pinning import ServerEntry, lookup

__all__ = [
    "ChatChannel",
    "connect",
    "lookup",
    "ServerEntry",
    "ClientError",
    "ConnectError",
    "ConnectTimeout",
    "ConnectRefused",
    "DnsError",
    "ServerKeyMismatch",
    "ProtocolError",
    "ServerDisconnected",
]
