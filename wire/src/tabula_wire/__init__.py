"""tabula-wire — transport layer (Noise XX, static keys, CLI).

This package owns the on-disk format for Tabula static keys and the top-level
``tabula`` CLI. See ``tabula_wire.crypto.keys`` for the canonical key loader
and ``tabula_wire.cli`` for the CLI entrypoint.

Subpackages (status as of issue #36):

- ``tabula_wire.errors``  — typed exception hierarchy + ErrorFrame mapping (#36)
- ``tabula_wire.proto``   — protobuf bindings (stub from #36; full from #16/#45)
- ``tabula_wire.crypto``  — static-key tooling (#46); Noise XX wrappers pending
- ``tabula_wire.cli``     — top-level ``tabula`` CLI entrypoint (#46)
- ``tabula_wire.framing`` — length-prefix codec (pending #55)
- ``tabula_wire.server``  — responder side (pending #49 / #55)
- ``tabula_wire.client``  — initiator side (pending #54 / #56)
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

__version__ = "0.1.0"

__all__ = [
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
]
