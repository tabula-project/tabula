"""Typed exception hierarchy for the chat client.

The acceptance criteria in #27 require connection-level failures to surface
as typed exceptions distinct from protocol-level failures so the CLI in #29
/ error-handler in #36 can offer the right user message for each.

This is the single canonical hierarchy reconciled across #27 (dialer) and
#54 (pinning store / ``tabula servers`` CLI).

Connection / handshake hierarchy::

    ClientError
    +-- ConnectTimeout       (TCP open or handshake exceeded the deadline)
    +-- ConnectRefused       (peer actively refused the TCP connection)
    +-- DnsError             (host could not be resolved)
    +-- ServerKeyMismatch    (handshake completed but pubkey != pinned)
    +-- ProtocolError        (decrypt failure, malformed proto, framing
                              violation, oversize frame, mid-stream EOF)
    +-- ServerDisconnected   (peer closed the transport mid-session)

Pinning-store hierarchy (added in #54)::

    PinningError
    +-- UnknownLabelError    (label not in the store; also a ``KeyError``)
    +-- DuplicateLabelError  (label already exists and ``force`` is False)
    +-- InvalidPubkeyError   (pubkey not 32 bytes / 64 lowercase hex chars;
                              also a ``ValueError``)

The two hierarchies are intentionally distinct: connection-level failures
are raised by :func:`tabula_wire.client.connect`, pinning-store failures are
raised by :mod:`tabula_wire.client.pinning`. Mid-handshake key mismatch is
:class:`ServerKeyMismatch` (the dialer raises it from the handshake path).
"""

from __future__ import annotations


# ---------- connection / handshake (#27) --------------------------------------


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


# ---------- pinning store (#32) -----------------------------------------------


class PinningError(Exception):
    """Base class for pinning-store errors."""


class UnknownLabelError(PinningError, KeyError):
    """Raised by ``remove`` (and surfaced by the chat connect path) when a
    label is not present in the pinning store.

    Inherits ``KeyError`` so callers that already handle ``KeyError`` for
    dict-style lookup see consistent behavior.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        super().__init__(label)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"unknown server label: {self.label!r}"


class DuplicateLabelError(PinningError):
    """Raised by ``add`` when ``label`` already exists and ``force`` is False."""

    def __init__(self, label: str) -> None:
        self.label = label
        super().__init__(label)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"server label {self.label!r} is already pinned; "
            "pass force=True (or `tabula servers add --force`) to overwrite"
        )


class InvalidPubkeyError(PinningError, ValueError):
    """Raised when a pubkey is not 32 bytes / 64 lowercase hex chars."""


__all__ = [
    # Connection / handshake (#27)
    "ClientError",
    "ConnectTimeout",
    "ConnectRefused",
    "DnsError",
    "ServerKeyMismatch",
    "ProtocolError",
    "ServerDisconnected",
    # Pinning store (#32)
    "PinningError",
    "UnknownLabelError",
    "DuplicateLabelError",
    "InvalidPubkeyError",
]
