"""tabula_wire.proto.v1 — generated protobuf bindings for the Noise-framed chat schema.

Regenerate with ``wire/proto/build.sh``. Do not hand-edit ``chat_pb2.py`` or
``chat_pb2.pyi``.

The wire schema is frozen at v1; new fields are additive only.
See ``wire/proto/README.md`` for the full v1 contract.
"""

from .chat_pb2 import (  # noqa: F401
    AssistantToken,
    AssistantTurnEnd,
    ClientFrame,
    EndSession,
    ErrorFrame,
    Hello,
    ServerFrame,
    UserMessage,
    Welcome,
)

# Wire protocol version. Carried in ``Hello.protocol_version`` and echoed in
# ``Welcome.protocol_version``. Bumped only on breaking changes.
PROTOCOL_VERSION = 1

__all__ = [
    "PROTOCOL_VERSION",
    "AssistantToken",
    "AssistantTurnEnd",
    "ClientFrame",
    "EndSession",
    "ErrorFrame",
    "Hello",
    "ServerFrame",
    "UserMessage",
    "Welcome",
]
