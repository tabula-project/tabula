"""Typed exception hierarchy for the chat client.

The acceptance criteria in #27 require connection-level failures to surface
as typed exceptions distinct from protocol-level failures so the CLI in #29
/ error-handler in #36 can offer the right user message for each.

Hierarchy::

    ClientError
    +-- ConnectTimeout       (TCP open or handshake exceeded the deadline)
    +-- ConnectRefused       (peer actively refused the TCP connection)
    +-- DnsError             (host could not be resolved)
    +-- ServerKeyMismatch    (handshake completed but pubkey != pinned)
    +-- ProtocolError        (decrypt failure, malformed proto, framing
                              violation, oversize frame, mid-stream EOF)
"""

from __future__ import annotations


class ClientError(Exception):
    """Base class for all errors raised by ``wire.client``."""


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


__all__ = [
    "ClientError",
    "ConnectTimeout",
    "ConnectRefused",
    "DnsError",
    "ServerKeyMismatch",
    "ProtocolError",
]
