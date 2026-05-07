"""tabula-wire — transport layer (Noise XX, static keys, protobuf schema, CLI).

This package owns the on-disk format for Tabula static keys, the
``tabula.wire.v1`` protobuf schema, and the top-level ``tabula`` CLI.
See ``tabula_wire.crypto.keys`` for the canonical key loader and
``tabula_wire.cli`` for the CLI entrypoint.

Subpackages:

- ``tabula_wire.errors``  — typed exception hierarchy + ErrorFrame mapping (#36)
- ``tabula_wire.proto``   — ``tabula.wire.v1`` schema + generated bindings (#16)
- ``tabula_wire.crypto``  — static-key tooling (#46); Noise XX wrappers pending (#18)
- ``tabula_wire.cli``     — top-level ``tabula`` CLI entrypoint (#46)
- ``tabula_wire.framing`` — length-prefix codec (pending #55)
- ``tabula_wire.server``  — responder side (pending #20 / #25)
- ``tabula_wire.client``  — initiator side (pending #27 / #32)

This module has no side effects on import.

Convenience re-exports of the wire protocol version and frame envelope types
are provided for callers that only need the schema surface; full protobuf
message types live under :mod:`tabula_wire.proto.v1`.
"""

from tabula_wire.errors import (
    AtCapacity,
    ClaudeCrashed,
    ClaudeTimeout,
    ClientDisconnected,
    HandshakeError,
    HandshakeTimeout,
    InternalError,
    MalformedFrame,
    MalformedHandshake,
    OversizeFrame,
    ServerDisconnected,
    ServerKeyMismatch,
    SessionError,
    SubprocessError,
    WireError,
    exception_to_error_code,
)
from tabula_wire.proto.v1 import (
    PROTOCOL_VERSION,
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

__version__ = "0.1.0"

__all__ = [
    # Exception hierarchy (#36)
    "WireError",
    "HandshakeError",
    "HandshakeTimeout",
    "ServerKeyMismatch",
    "MalformedHandshake",
    "SessionError",
    "ServerDisconnected",
    "ClientDisconnected",
    "OversizeFrame",
    "MalformedFrame",
    "SubprocessError",
    "ClaudeCrashed",
    "ClaudeTimeout",
    "AtCapacity",
    "InternalError",
    "exception_to_error_code",
    # Wire schema (#16)
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
    "__version__",
]
