"""Stub chat frame types pending the protobuf schema in #16.

This module exists so #27 (the client dialer) can compile and be tested
without blocking on #16. Once #16 lands, the real generated bindings under
``wire.proto.gen`` will replace the stub types here, and callers that
import from ``wire.proto`` will be source-compatible.

Stub semantics (intentionally minimal):
- ``ClientFrame`` and ``ServerFrame`` are dataclasses carrying a ``payload``
  ``bytes`` field plus a ``kind`` discriminator string.
- ``serialize`` / ``deserialize`` use a tiny self-describing format:
  ``len(kind):1 | kind | payload``.

The real proto types from #16 will expose ``SerializeToString`` /
``ParseFromString`` and a ``oneof payload`` of variant submessages. Callers
in this issue treat frames as opaque "encode to bytes / decode from bytes"
units, so the swap is mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ProtoError(Exception):
    """Raised when a frame cannot be serialized or deserialized."""


@dataclass
class ClientFrame:
    """Stub for the protobuf ``ClientFrame`` message.

    Real version (per #16) is a oneof of ``Hello`` / ``UserMessage`` /
    ``EndSession``. The stub keeps only the bare minimum: a ``kind`` tag
    naming which variant this is and a ``payload`` bytes blob.
    """

    kind: str = "UserMessage"
    payload: bytes = b""

    def SerializeToString(self) -> bytes:  # noqa: N802 - matches proto API
        return _serialize(self.kind, self.payload)

    @classmethod
    def FromString(cls, data: bytes) -> "ClientFrame":  # noqa: N802
        kind, payload = _deserialize(data)
        return cls(kind=kind, payload=payload)


@dataclass
class ServerFrame:
    """Stub for the protobuf ``ServerFrame`` message.

    Real version (per #16) is a oneof of ``Welcome`` / ``AssistantToken`` /
    ``AssistantTurnEnd`` / ``ErrorFrame``.
    """

    kind: str = "AssistantToken"
    payload: bytes = b""

    def SerializeToString(self) -> bytes:  # noqa: N802
        return _serialize(self.kind, self.payload)

    @classmethod
    def FromString(cls, data: bytes) -> "ServerFrame":  # noqa: N802
        kind, payload = _deserialize(data)
        return cls(kind=kind, payload=payload)


def _serialize(kind: str, payload: bytes) -> bytes:
    kb = kind.encode("utf-8")
    if len(kb) > 255:
        raise ProtoError("kind too long for stub format")
    return bytes([len(kb)]) + kb + payload


def _deserialize(data: bytes) -> tuple[str, bytes]:
    if len(data) < 1:
        raise ProtoError("empty frame")
    klen = data[0]
    if len(data) < 1 + klen:
        raise ProtoError("truncated kind")
    kind = data[1 : 1 + klen].decode("utf-8")
    payload = data[1 + klen :]
    return kind, payload


__all__ = ["ClientFrame", "ServerFrame", "ProtoError"]
