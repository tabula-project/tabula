"""tabula_wire.proto.v1 — generated protobuf bindings for the Noise-framed chat schema.

Regenerate with ``wire/proto/build.sh``. Do not hand-edit ``chat_pb2.py`` or
``chat_pb2.pyi``.

The wire schema is frozen at v1; new fields are additive only.
See ``wire/proto/README.md`` for the full v1 contract.

In addition to re-exporting the generated message classes, this module also
exposes module-level aliases consumed by the session manager (#25) and
related code:

- ``ErrorCode`` — alias for ``ErrorFrame.Code`` (so callers can write
  ``ErrorCode.PROTOCOL`` directly without reaching through ``ErrorFrame``).
- ``FinishReason`` — alias for ``AssistantTurnEnd.FinishReason``.
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

# Module-level aliases for nested enums on the generated message classes.
# These match the post-#45 idiom where callers reach for the nested enum
# directly (e.g. ``ErrorFrame.Code.PROTOCOL``) but prefer the shorter
# top-level name in hot paths like the session manager.
ErrorCode = ErrorFrame.Code
FinishReason = AssistantTurnEnd.FinishReason

__all__ = [
    "PROTOCOL_VERSION",
    "AssistantToken",
    "AssistantTurnEnd",
    "ClientFrame",
    "EndSession",
    "ErrorCode",
    "ErrorFrame",
    "FinishReason",
    "Hello",
    "ServerFrame",
    "UserMessage",
    "Welcome",
]
