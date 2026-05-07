"""tabula-wire — transport layer (Noise XX, static keys, protobuf schema).

This package owns the on-disk format for Tabula static keys and the
``tabula.wire.v1`` protobuf schema. See ``tabula_wire.crypto.keys`` for
the canonical key loader. The user-facing ``tabula`` CLI (including the
``keygen`` subcommand originally landed in #46) lives in the
``tabula-cli`` distribution per architect proposal #57; see
``tabula_cli.keygen`` for the CLI entry point.

Subpackages:

- ``tabula_wire.errors``  — typed exception hierarchy + ErrorFrame mapping (#36)
- ``tabula_wire.proto``   — ``tabula.wire.v1`` schema + generated bindings (#16)
- ``tabula_wire.crypto``  — static-key tooling (#46); Noise XX wrappers (#18)
- ``tabula_wire.framing`` — length-prefix codec (#55)
- ``tabula_wire.server``  — responder side (#20 / #25)
- ``tabula_wire.client``  — initiator side (#32 pinning store; dialer #27/#56)

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
from tabula_wire.client import (
    DuplicateLabelError,
    InvalidPubkeyError,
    PinningError,
    ServerEntry,
    UnknownLabelError,
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
    # Client pinning store (#32)
    "DuplicateLabelError",
    "InvalidPubkeyError",
    "PinningError",
    "ServerEntry",
    "UnknownLabelError",
    "__version__",
]
