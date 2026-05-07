"""Typed exception hierarchy for the chat client.

The acceptance criteria in #27 require connection-level failures to surface
as typed exceptions distinct from protocol-level failures so the CLI in #29
/ error-handler in #36 can offer the right user message for each.

This is the single canonical hierarchy reconciled across #27 (dialer) and
#54 (pinning store / ``tabula servers`` CLI).

Hierarchy::

    ClientError
    +-- ConnectTimeout       (TCP open or handshake exceeded the deadline)
    +-- ConnectRefused       (peer actively refused the TCP connection)
    +-- DnsError             (host could not be resolved)
    +-- ServerKeyMismatch    (handshake completed but pubkey != pinned)
    +-- ProtocolError        (decrypt failure, malformed proto, framing
                              violation, oversize frame, mid-stream EOF)
    +-- ServerDisconnected   (peer closed the transport mid-session)
"""

from __future__ import annotations


class ClientError(Exception):
    """Base class for all errors raised by ``tabula_wire.client``."""


class ConnectTimeout(ClientError):
    """The dial did not complete (TCP open + handshake) before the deadline."""


class ConnectRefused(ClientError):
    """The OS reported ``ECONNREFUSED`` (or equivalent) on dial."""


class DnsError(ClientError):
    """The host could not be resolved."""


class ServerKeyMismatch(ClientError):
    """The Noise XX handshake completed but the responder's static public key
    does not match the pinned value supplied by the caller."""


class ProtocolError(ClientError):
    """A wire-level protocol violation: decrypt failure, malformed proto,
    bad framing, oversize frame, or mid-stream EOF."""


class ServerDisconnected(ClientError):
    """The server closed the transport mid-session (clean EOF after handshake)."""


__all__ = [
    "ClientError",
    "ConnectTimeout",
    "ConnectRefused",
    "DnsError",
    "ServerKeyMismatch",
    "ProtocolError",
    "ServerDisconnected",
]
