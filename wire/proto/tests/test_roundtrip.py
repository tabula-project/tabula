"""Round-trip serialize/deserialize tests for tabula.wire.v1.

For each variant of the `ClientFrame` and `ServerFrame` `oneof payload`,
we build the message, serialize to bytes via protobuf, deserialize, and
assert the decoded message is byte-equal to the original.

These tests are deliberately framing-agnostic — they exercise only the
proto schema, not the length-prefix or Noise transport. Framing tests
live with the server/client implementations (see parent epic #13).
"""

from __future__ import annotations

import pathlib
import sys

# Make `tabula.wire.v1` importable when running pytest from the repo root.
# Generated bindings live in wire/proto/gen/.
_GEN_DIR = pathlib.Path(__file__).resolve().parents[1] / "gen"
if str(_GEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GEN_DIR))

import pytest

from tabula.wire.v1 import (  # noqa: E402
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


def _roundtrip_client(frame: ClientFrame) -> ClientFrame:
    """Serialize and deserialize a ClientFrame; return the decoded copy."""
    encoded = frame.SerializeToString()
    decoded = ClientFrame()
    decoded.ParseFromString(encoded)
    return decoded


def _roundtrip_server(frame: ServerFrame) -> ServerFrame:
    """Serialize and deserialize a ServerFrame; return the decoded copy."""
    encoded = frame.SerializeToString()
    decoded = ServerFrame()
    decoded.ParseFromString(encoded)
    return decoded


# ---------------------------------------------------------------------------
# Protocol version
# ---------------------------------------------------------------------------


def test_protocol_version_constant_is_1() -> None:
    """v1 contract: the constant exposed alongside the bindings is 1."""
    assert PROTOCOL_VERSION == 1


# ---------------------------------------------------------------------------
# ClientFrame variants
# ---------------------------------------------------------------------------


def test_client_frame_hello_roundtrip() -> None:
    frame = ClientFrame(
        hello=Hello(
            protocol_version=PROTOCOL_VERSION,
            client_identity="tabula-cli/0.1.0 (darwin)",
            auth_token=b"\x00\x01\x02\x03",
        )
    )
    decoded = _roundtrip_client(frame)
    assert decoded == frame
    assert decoded.WhichOneof("payload") == "hello"
    assert decoded.hello.protocol_version == 1
    assert decoded.hello.client_identity == "tabula-cli/0.1.0 (darwin)"
    assert decoded.hello.auth_token == b"\x00\x01\x02\x03"


def test_client_frame_user_message_roundtrip() -> None:
    frame = ClientFrame(
        user_message=UserMessage(
            text="hello, what's in this repo?",
            turn_id="01HXY7EXAMPLEULID0000000",
        )
    )
    decoded = _roundtrip_client(frame)
    assert decoded == frame
    assert decoded.WhichOneof("payload") == "user_message"
    assert decoded.user_message.text == "hello, what's in this repo?"
    assert decoded.user_message.turn_id == "01HXY7EXAMPLEULID0000000"


def test_client_frame_end_session_roundtrip() -> None:
    frame = ClientFrame(end=EndSession(reason="user pressed ctrl-d"))
    decoded = _roundtrip_client(frame)
    assert decoded == frame
    assert decoded.WhichOneof("payload") == "end"
    assert decoded.end.reason == "user pressed ctrl-d"


def test_client_frame_unset_payload_roundtrip() -> None:
    """A ClientFrame with no oneof set MUST roundtrip without raising.

    Servers should treat this as a malformed frame and respond with
    ErrorFrame{CODE_MALFORMED_FRAME}, but the proto layer itself
    must accept the bytes.
    """
    frame = ClientFrame()
    decoded = _roundtrip_client(frame)
    assert decoded == frame
    assert decoded.WhichOneof("payload") is None


# ---------------------------------------------------------------------------
# ServerFrame variants
# ---------------------------------------------------------------------------


def test_server_frame_welcome_roundtrip() -> None:
    frame = ServerFrame(
        welcome=Welcome(
            server_identity="tabula-enclave-demo/sha:abc1234",
            session_id="01HXY8SESSIONULID000000000",
            protocol_version=PROTOCOL_VERSION,
        )
    )
    decoded = _roundtrip_server(frame)
    assert decoded == frame
    assert decoded.WhichOneof("payload") == "welcome"
    assert decoded.welcome.server_identity == "tabula-enclave-demo/sha:abc1234"
    assert decoded.welcome.session_id == "01HXY8SESSIONULID000000000"
    assert decoded.welcome.protocol_version == 1


def test_server_frame_assistant_token_roundtrip() -> None:
    frame = ServerFrame(
        token=AssistantToken(
            text="Sure, this repo contains ",
            sequence=42,
            turn_id="01HXY7EXAMPLEULID0000000",
        )
    )
    decoded = _roundtrip_server(frame)
    assert decoded == frame
    assert decoded.WhichOneof("payload") == "token"
    assert decoded.token.text == "Sure, this repo contains "
    assert decoded.token.sequence == 42
    assert decoded.token.turn_id == "01HXY7EXAMPLEULID0000000"


def test_server_frame_assistant_turn_end_roundtrip() -> None:
    frame = ServerFrame(
        turn_end=AssistantTurnEnd(
            finish_reason=AssistantTurnEnd.FINISH_REASON_STOP,
            turn_id="01HXY7EXAMPLEULID0000000",
        )
    )
    decoded = _roundtrip_server(frame)
    assert decoded == frame
    assert decoded.WhichOneof("payload") == "turn_end"
    assert decoded.turn_end.finish_reason == AssistantTurnEnd.FINISH_REASON_STOP
    assert decoded.turn_end.turn_id == "01HXY7EXAMPLEULID0000000"


def test_server_frame_error_roundtrip() -> None:
    frame = ServerFrame(
        error=ErrorFrame(
            code=ErrorFrame.CODE_PROTOCOL_VERSION_UNSUPPORTED,
            message="server speaks v1; client requested v999",
            turn_id="",
        )
    )
    decoded = _roundtrip_server(frame)
    assert decoded == frame
    assert decoded.WhichOneof("payload") == "error"
    assert decoded.error.code == ErrorFrame.CODE_PROTOCOL_VERSION_UNSUPPORTED
    assert decoded.error.message == "server speaks v1; client requested v999"


def test_server_frame_unset_payload_roundtrip() -> None:
    """A ServerFrame with no oneof set MUST roundtrip without raising."""
    frame = ServerFrame()
    decoded = _roundtrip_server(frame)
    assert decoded == frame
    assert decoded.WhichOneof("payload") is None


# ---------------------------------------------------------------------------
# Enum sanity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        AssistantTurnEnd.FINISH_REASON_UNSPECIFIED,
        AssistantTurnEnd.FINISH_REASON_STOP,
        AssistantTurnEnd.FINISH_REASON_LENGTH,
        AssistantTurnEnd.FINISH_REASON_USER_CANCELED,
        AssistantTurnEnd.FINISH_REASON_ERROR,
    ],
)
def test_finish_reason_enum_values_roundtrip(reason: int) -> None:
    frame = ServerFrame(turn_end=AssistantTurnEnd(finish_reason=reason))
    decoded = _roundtrip_server(frame)
    assert decoded.turn_end.finish_reason == reason


@pytest.mark.parametrize(
    "code",
    [
        ErrorFrame.CODE_UNSPECIFIED,
        ErrorFrame.CODE_PROTOCOL_VERSION_UNSUPPORTED,
        ErrorFrame.CODE_MALFORMED_FRAME,
        ErrorFrame.CODE_INTERNAL,
        ErrorFrame.CODE_SUBPROCESS_FAILED,
        ErrorFrame.CODE_RATE_LIMITED,
        ErrorFrame.CODE_UNAUTHORIZED,
    ],
)
def test_error_code_enum_values_roundtrip(code: int) -> None:
    frame = ServerFrame(error=ErrorFrame(code=code, message="x"))
    decoded = _roundtrip_server(frame)
    assert decoded.error.code == code
