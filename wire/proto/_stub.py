"""Stub stand-ins for the protobuf-generated ``ClientFrame`` /
``ServerFrame`` from issue #16.

Wire format (stub): a single line of UTF-8 ``key=value`` pairs separated
by ``\\n``, terminated by an empty line. The real bindings will replace
this entirely; the listener only sees these objects through the
``FrameCodec`` protocol so the swap is mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class _ProtoStubBase:
    """Minimal protobuf-message-like surface."""

    _fields: tuple[str, ...] = ()

    def SerializeToString(self) -> bytes:  # noqa: N802 - mimic protobuf API
        parts: list[str] = [self.__class__.__name__]
        for name in self._fields:
            value = getattr(self, name)
            if isinstance(value, bytes):
                value = value.hex()
            parts.append(f"{name}={value}")
        return ("\n".join(parts) + "\n").encode("utf-8")

    @classmethod
    def ParseFromString(cls, data: bytes):  # noqa: N802 - mimic protobuf API
        text = data.decode("utf-8")
        lines = [line for line in text.split("\n") if line]
        if not lines or lines[0] != cls.__name__:
            raise ValueError(
                f"malformed stub frame: expected {cls.__name__}, got {lines[:1]}"
            )
        kwargs: dict[str, object] = {}
        for line in lines[1:]:
            if "=" not in line:
                raise ValueError(f"malformed stub frame field: {line!r}")
            k, v = line.split("=", 1)
            if k not in cls._fields:
                raise ValueError(f"unknown field {k!r} for {cls.__name__}")
            kwargs[k] = v
        return cls(**kwargs)  # type: ignore[arg-type]


@dataclass(eq=True)
class ClientFrame(_ProtoStubBase):
    """Stub of ``tabula.wire.v1.ClientFrame``.

    Real bindings will have a ``oneof payload`` of Hello / UserMessage /
    EndSession (per #16). For #20 we only need a single string field —
    the listener treats frames as opaque.
    """

    kind: str = ""
    text: str = ""

    _fields = ("kind", "text")


@dataclass(eq=True)
class ServerFrame(_ProtoStubBase):
    """Stub of ``tabula.wire.v1.ServerFrame``."""

    kind: str = ""
    text: str = ""

    _fields = ("kind", "text")


@runtime_checkable
class FrameCodec(Protocol):
    """Codec the listener uses to (de)serialize plaintext frames.

    Both stubs and real protobuf bindings can satisfy this. The
    listener never imports concrete frame classes — it accepts a codec
    instance from its caller (test or session manager).
    """

    def encode_server_frame(self, frame: object) -> bytes:
        """Serialize a ServerFrame to bytes for encryption."""
        ...

    def decode_client_frame(self, data: bytes) -> object:
        """Parse bytes (decrypted plaintext) into a ClientFrame."""
        ...


@dataclass
class StubProtoCodec:
    """``FrameCodec`` impl for the stub frame classes above."""

    client_cls: type = field(default=ClientFrame)
    server_cls: type = field(default=ServerFrame)

    def encode_server_frame(self, frame: object) -> bytes:
        if not isinstance(frame, self.server_cls):
            raise TypeError(
                f"expected {self.server_cls.__name__}, got {type(frame).__name__}"
            )
        return frame.SerializeToString()

    def decode_client_frame(self, data: bytes):
        return self.client_cls.ParseFromString(data)
