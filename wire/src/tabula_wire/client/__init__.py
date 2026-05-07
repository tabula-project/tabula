"""Tabula chat client transport.

Public surface:

- :func:`connect` (#27) — async TCP dialer that runs the Noise XX initiator
  handshake, verifies the responder's static pubkey against a pinned value,
  and returns a :class:`ChatChannel`.
- :class:`ChatChannel` (#27) — async send/recv/close interface over a Noise
  transport stream.
- :mod:`tabula_wire.client.pinning` (#32) — the on-disk server pubkey
  pinning store at ``~/.config/tabula/known_servers``. Re-exports its
  public surface for ergonomic access.
- Typed exceptions: :class:`ConnectError` (base for connect-phase failures)
  with subclasses :class:`ConnectTimeout`, :class:`ConnectRefused`,
  :class:`DnsError`; :class:`ServerKeyMismatch`, :class:`ProtocolError`,
  :class:`ClientError` (base) for the dialer; :class:`PinningError`,
  :class:`UnknownLabelError`, :class:`DuplicateLabelError`,
  :class:`InvalidPubkeyError` for the pinning store.
"""

from __future__ import annotations

from .dialer import ChatChannel, connect
from .exceptions import (
    ClientError,
    ConnectError,
    ConnectRefused,
    ConnectTimeout,
    DnsError,
    DuplicateLabelError,
    InvalidPubkeyError,
    PinningError,
    ProtocolError,
    ServerDisconnected,
    ServerKeyMismatch,
    UnknownLabelError,
)
from .pinning import (
    DEFAULT_STORE_HEADER,
    ServerEntry,
    add,
    config_dir,
    list_all,
    lookup,
    mismatch_error_message,
    refuse_unknown_label_message,
    remove,
    store_path,
)

__all__ = [
    # Dialer (#27)
    "ChatChannel",
    "connect",
    # Connection / handshake exceptions (#27)
    "ClientError",
    "ConnectError",
    "ConnectRefused",
    "ConnectTimeout",
    "DnsError",
    "ProtocolError",
    "ServerDisconnected",
    "ServerKeyMismatch",
    # Pinning store (#32)
    "DEFAULT_STORE_HEADER",
    "ServerEntry",
    "add",
    "config_dir",
    "list_all",
    "lookup",
    "mismatch_error_message",
    "refuse_unknown_label_message",
    "remove",
    "store_path",
    # Pinning-store exceptions (#32)
    "PinningError",
    "UnknownLabelError",
    "DuplicateLabelError",
    "InvalidPubkeyError",
]
