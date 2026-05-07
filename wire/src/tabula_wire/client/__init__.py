"""Tabula chat client transport.

Public surface (this issue, #27):
- :func:`connect` — async TCP dialer that runs the Noise XX initiator
  handshake, verifies the responder's static pubkey against a pinned value,
  and returns a :class:`ChatChannel`.
- :class:`ChatChannel` — async send/recv/close interface over a Noise
  transport stream.
- Typed exceptions: :class:`ConnectTimeout`, :class:`ConnectRefused`,
  :class:`DnsError`, :class:`ServerKeyMismatch`, :class:`ProtocolError`,
  :class:`ClientError` (base).
"""

from __future__ import annotations

from .dialer import ChatChannel, connect
from .exceptions import (
    ClientError,
    ConnectRefused,
    ConnectTimeout,
    DnsError,
    ProtocolError,
    ServerDisconnected,
    ServerKeyMismatch,
)

__all__ = [
    "ChatChannel",
    "ClientError",
    "ConnectRefused",
    "ConnectTimeout",
    "DnsError",
    "ProtocolError",
    "ServerDisconnected",
    "ServerKeyMismatch",
    "connect",
]
