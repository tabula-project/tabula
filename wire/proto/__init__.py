"""Protobuf bindings for tabula.wire.v1.

The real generated bindings land in #16. Until then, this package
exposes lightweight stubs for ``ClientFrame`` and ``ServerFrame`` with
the same public surface (`SerializeToString`, `ParseFromString`,
`__eq__`) the listener uses. The listener depends on a structural
``FrameCodec`` protocol — not on these stub classes — so swapping in
the real protobuf bindings from #16 only requires replacing the codec
factory in ``wire.server.listener``.

Integration with #16
--------------------
When generated bindings ``wire/proto/gen/chat_pb2.py`` are checked in:

1. Replace this module's stubs by re-exporting the real ``ClientFrame``
   and ``ServerFrame`` from ``wire.proto.gen.chat_pb2``.
2. Drop ``wire/proto/_stub.py``.
3. Listener tests parametrize over a codec factory; only that factory
   changes.
"""

from ._stub import (
    ClientFrame,
    FrameCodec,
    ServerFrame,
    StubProtoCodec,
)

__all__ = ["ClientFrame", "FrameCodec", "ServerFrame", "StubProtoCodec"]
