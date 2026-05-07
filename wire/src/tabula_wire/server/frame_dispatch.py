"""Server-side frame dispatch helpers.

This module is *not* the length-prefix codec (that lives in
:mod:`tabula_wire.framing` per #55). It is the protobuf-level oneof
dispatch surface: re-exports the v1 frame types so the session manager
can pattern-match on payload variants without reaching into the
generated proto namespace directly.

Once #45 lands, :mod:`tabula_wire.proto.v1` is the generated package; the
re-export below is a stable seam for the session manager.
"""

from __future__ import annotations

from tabula_wire.proto.v1 import (
    AssistantToken,
    AssistantTurnEnd,
    ClientFrame,
    EndSession,
    ErrorCode,
    ErrorFrame,
    FinishReason,
    Hello,
    ServerFrame,
    UserMessage,
    Welcome,
)

__all__ = [
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
