"""Stub frame types mirroring tabula.wire.v1 (#16).

These dataclasses stand in for protobuf-generated bindings until #16 lands.
The wire-level oneof is modelled by distinct classes; the chat UI matches
on `isinstance` rather than poking a `WhichOneof` field, which keeps the
swap to real protobuf bindings (a) optional and (b) syntactically local
(callers can `from wire.proto import AssistantToken` either way).

When #16 merges, this module should re-export the generated message types
and delete the dataclass stubs. Field names below match the names locked
in the epic (#13) so the swap is mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass


# --- ClientFrame oneof variants -------------------------------------------------


@dataclass(frozen=True)
class Hello:
    """Initial client frame: protocol version + opaque client identity hint."""

    protocol_version: int = 1
    client_identity_hint: str = ""


@dataclass(frozen=True)
class UserMessage:
    """One line of user input."""

    text: str


@dataclass(frozen=True)
class EndSession:
    """Client signals graceful close. Server acks by closing the stream."""

    reason: str = ""


# --- ServerFrame oneof variants -------------------------------------------------


@dataclass(frozen=True)
class Welcome:
    """Server handshake ack. Carries the negotiated session id."""

    session_id: str
    server_identity_hint: str = ""


@dataclass(frozen=True)
class AssistantToken:
    """Streamed token chunk. `seq` is monotonic per assistant turn."""

    text: str
    seq: int = 0


@dataclass(frozen=True)
class AssistantTurnEnd:
    """End-of-turn marker. `finish_reason` is informational."""

    finish_reason: str = "stop"


@dataclass(frozen=True)
class ErrorFrame:
    """Server-side fatal error. Code is machine-readable; message is human."""

    code: str
    message: str = ""


# --- Frame unions (typing convenience) ------------------------------------------

ClientFrame = Hello | UserMessage | EndSession
ServerFrame = Welcome | AssistantToken | AssistantTurnEnd | ErrorFrame


__all__ = [
    "Hello",
    "UserMessage",
    "EndSession",
    "Welcome",
    "AssistantToken",
    "AssistantTurnEnd",
    "ErrorFrame",
    "ClientFrame",
    "ServerFrame",
]
