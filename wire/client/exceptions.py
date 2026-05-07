"""Typed client-side exception hierarchy (stub matching #27 contract).

Names finalized here are the names #29 chat UI catches. #27's real dialer
should raise the same types so this stub can be deleted in favour of
re-exports without touching the UI.
"""

from __future__ import annotations


class ClientError(Exception):
    """Base for all wire-client errors raised to the UI."""


class ConnectError(ClientError):
    """Base for transport-level failures during connect."""


class ConnectTimeout(ConnectError):
    """Connect or handshake exceeded the configured timeout."""


class ConnectRefused(ConnectError):
    """Peer refused the TCP connection."""


class DnsError(ConnectError):
    """Hostname resolution failed."""


class ServerKeyMismatch(ClientError):
    """Server static pubkey did not match the pinned value."""


class ProtocolError(ClientError):
    """Decrypt failed, malformed proto, or violated frame ordering."""


class ServerDisconnected(ClientError):
    """Server closed the stream mid-session (after Welcome, before EndSession ack)."""


__all__ = [
    "ClientError",
    "ConnectError",
    "ConnectTimeout",
    "ConnectRefused",
    "DnsError",
    "ServerKeyMismatch",
    "ProtocolError",
    "ServerDisconnected",
]
