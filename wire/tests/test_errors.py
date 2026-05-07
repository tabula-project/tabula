"""Unit tests for ``tabula_wire.errors``.

Covers the typed exception hierarchy structure, structured fields,
message rendering, and the ``exception_to_error_code`` mapping table.
These are pure-Python tests with no transport dependencies.
"""

from __future__ import annotations

import pytest

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
    build_error_frame,
    exception_to_error_code,
)
from tabula_wire.proto.v1 import ErrorFrame


# ---------------------------------------------------------------------------
# Hierarchy shape
# ---------------------------------------------------------------------------


class TestHierarchyShape:
    """Inheritance must match the structure described in #36."""

    def test_handshake_family_descends_from_wire_error(self) -> None:
        for cls in (HandshakeError, HandshakeTimeout, ServerKeyMismatch, MalformedHandshake):
            assert issubclass(cls, WireError), cls

    def test_session_family_descends_from_wire_error(self) -> None:
        for cls in (
            SessionError,
            ServerDisconnected,
            ClientDisconnected,
            OversizeFrame,
            MalformedFrame,
        ):
            assert issubclass(cls, WireError), cls

    def test_subprocess_family_descends_from_wire_error(self) -> None:
        for cls in (SubprocessError, ClaudeCrashed, ClaudeTimeout):
            assert issubclass(cls, WireError), cls

    def test_handshake_subclasses_belong_to_handshake_error(self) -> None:
        for cls in (HandshakeTimeout, ServerKeyMismatch, MalformedHandshake):
            assert issubclass(cls, HandshakeError)

    def test_session_subclasses_belong_to_session_error(self) -> None:
        for cls in (ServerDisconnected, ClientDisconnected, OversizeFrame, MalformedFrame):
            assert issubclass(cls, SessionError)

    def test_subprocess_subclasses_belong_to_subprocess_error(self) -> None:
        for cls in (ClaudeCrashed, ClaudeTimeout):
            assert issubclass(cls, SubprocessError)

    def test_at_capacity_and_internal_error_are_wire_errors(self) -> None:
        assert issubclass(AtCapacity, WireError)
        assert issubclass(InternalError, WireError)
        # But NOT under the three families above.
        assert not issubclass(AtCapacity, HandshakeError)
        assert not issubclass(AtCapacity, SessionError)
        assert not issubclass(AtCapacity, SubprocessError)


# ---------------------------------------------------------------------------
# Structured fields and message formatting
# ---------------------------------------------------------------------------


class TestHandshakeTimeout:
    def test_carries_timeout_and_peer(self) -> None:
        exc = HandshakeTimeout(timeout_s=10.0, peer="1.2.3.4:5678")
        assert exc.timeout_s == 10.0
        assert exc.peer == "1.2.3.4:5678"
        assert "10.0s" in str(exc)
        assert "1.2.3.4:5678" in str(exc)

    def test_peer_is_optional(self) -> None:
        exc = HandshakeTimeout(timeout_s=5.5)
        assert exc.peer == ""
        assert "5.5s" in str(exc)


class TestServerKeyMismatch:
    """Per #36 acceptance criteria, message must contain BOTH keys in hex."""

    def test_renders_both_keys_in_hex(self) -> None:
        expected = b"\xaa" * 32
        actual = b"\xbb" * 32
        exc = ServerKeyMismatch(expected_key=expected, actual_key=actual, server="enclave-x")

        msg = str(exc)
        assert expected.hex() in msg
        assert actual.hex() in msg
        assert "enclave-x" in msg

    def test_keys_are_stored_as_bytes(self) -> None:
        exc = ServerKeyMismatch(expected_key=b"\x01\x02", actual_key=b"\x03\x04")
        assert isinstance(exc.expected_key, bytes)
        assert isinstance(exc.actual_key, bytes)


class TestMalformedHandshake:
    def test_carries_reason_bytes_and_peer(self) -> None:
        exc = MalformedHandshake(reason="bad magic", bytes_seen=4, peer="10.0.0.1:9999")
        assert exc.reason == "bad magic"
        assert exc.bytes_seen == 4
        assert "bad magic" in str(exc)
        assert "10.0.0.1:9999" in str(exc)


class TestOversizeFrame:
    def test_carries_advertised_and_limit(self) -> None:
        exc = OversizeFrame(advertised=10_000_000, limit=1_048_576)
        assert exc.advertised == 10_000_000
        assert exc.limit == 1_048_576
        assert "10000000" in str(exc)
        assert "1048576" in str(exc)


class TestMalformedFrame:
    def test_carries_reason_and_kind(self) -> None:
        exc = MalformedFrame(reason="ParseFromString failed", frame_kind="ClientFrame")
        assert "ClientFrame" in str(exc)
        assert "ParseFromString failed" in str(exc)


class TestServerDisconnected:
    def test_default_during(self) -> None:
        exc = ServerDisconnected()
        assert exc.during == "frame_loop"
        assert "frame_loop" in str(exc)

    def test_custom_during(self) -> None:
        exc = ServerDisconnected(during="welcome")
        assert "welcome" in str(exc)


class TestClientDisconnected:
    def test_carries_session_id(self) -> None:
        exc = ClientDisconnected(during="assistant_stream", session_id="01HX-SESS-001")
        assert "01HX-SESS-001" in str(exc)
        assert "assistant_stream" in str(exc)


class TestClaudeCrashed:
    def test_carries_returncode_and_stderr_tail(self) -> None:
        exc = ClaudeCrashed(returncode=137, stderr_tail="line1\nFATAL: out of memory\n")
        assert exc.returncode == 137
        msg = str(exc)
        assert "137" in msg
        assert "FATAL: out of memory" in msg

    def test_no_stderr_is_ok(self) -> None:
        exc = ClaudeCrashed(returncode=1)
        assert "1" in str(exc)


class TestClaudeTimeout:
    def test_carries_idle_timeout(self) -> None:
        exc = ClaudeTimeout(idle_timeout_s=60.0)
        assert exc.idle_timeout_s == 60.0
        assert "60.0s" in str(exc)


class TestAtCapacity:
    def test_carries_current_and_limit(self) -> None:
        exc = AtCapacity(current=8, limit=8)
        assert exc.current == 8
        assert exc.limit == 8
        assert "8/8" in str(exc)


# ---------------------------------------------------------------------------
# Mapping table
# ---------------------------------------------------------------------------


class TestExceptionToErrorCode:
    """Per #36, every server-side typed exception maps to an ErrorFrame.Code."""

    @pytest.mark.parametrize(
        ("exc_factory", "expected_code"),
        [
            # Subprocess family
            (lambda: ClaudeCrashed(returncode=1), ErrorFrame.Code.CLAUDE_CRASHED),
            (lambda: ClaudeTimeout(idle_timeout_s=60.0), ErrorFrame.Code.CLAUDE_TIMEOUT),
            # Session family — protocol violations
            (lambda: OversizeFrame(advertised=2_000_000, limit=1_048_576), ErrorFrame.Code.PROTOCOL),
            (lambda: MalformedFrame(reason="bad bytes"), ErrorFrame.Code.PROTOCOL),
            # Handshake family
            (lambda: MalformedHandshake(reason="bad magic"), ErrorFrame.Code.PROTOCOL),
            (lambda: HandshakeTimeout(timeout_s=10.0), ErrorFrame.Code.PROTOCOL),
            # Capacity
            (lambda: AtCapacity(current=8, limit=8), ErrorFrame.Code.AT_CAPACITY),
            # Internal catch-all
            (lambda: InternalError("boom"), ErrorFrame.Code.INTERNAL),
        ],
    )
    def test_mapping(self, exc_factory, expected_code: ErrorFrame.Code) -> None:
        assert exception_to_error_code(exc_factory()) == expected_code

    def test_unknown_exception_type_falls_back_to_internal(self) -> None:
        # A non-WireError exception should fall back to INTERNAL even if it
        # somehow reached the translator (the session loop wraps these in
        # InternalError before calling, but defense in depth matters).
        assert exception_to_error_code(RuntimeError("surprise")) == ErrorFrame.Code.INTERNAL

    def test_subclass_of_subclass_resolves_correctly(self) -> None:
        """A user-defined subclass of, say, ClaudeCrashed should still map to
        CLAUDE_CRASHED — not be silently rerouted to INTERNAL."""

        class MoreSpecificCrash(ClaudeCrashed):
            pass

        exc = MoreSpecificCrash(returncode=42)
        assert exception_to_error_code(exc) == ErrorFrame.Code.CLAUDE_CRASHED

    def test_specific_overrides_family(self) -> None:
        """ClaudeCrashed maps to CLAUDE_CRASHED, NOT to the
        SubprocessError family default. Resolution must walk MRO leaf-first."""
        assert exception_to_error_code(ClaudeCrashed(returncode=1)) == ErrorFrame.Code.CLAUDE_CRASHED
        # Sanity: the SubprocessError family default would also be CLAUDE_CRASHED
        # but a generic SubprocessError instance should still get the family default.
        generic = SubprocessError("some subprocess fault")
        assert exception_to_error_code(generic) == ErrorFrame.Code.CLAUDE_CRASHED


class TestBuildErrorFrame:
    def test_uses_exception_message(self) -> None:
        exc = OversizeFrame(advertised=2_000_000, limit=1_048_576)
        frame = build_error_frame(exc)
        assert frame.code == ErrorFrame.Code.PROTOCOL
        assert "2000000" in frame.message
        assert frame.turn_id == ""

    def test_carries_turn_id_when_provided(self) -> None:
        exc = ClaudeCrashed(returncode=1)
        frame = build_error_frame(exc, turn_id="01HX-TURN-7")
        assert frame.code == ErrorFrame.Code.CLAUDE_CRASHED
        assert frame.turn_id == "01HX-TURN-7"


# ---------------------------------------------------------------------------
# Catchability invariants
# ---------------------------------------------------------------------------


class TestCatchability:
    """Exception handlers in server/client code rely on catching
    family-level types. Verify that ``except SubprocessError`` etc. work."""

    def test_caught_as_subprocess_error(self) -> None:
        with pytest.raises(SubprocessError):
            raise ClaudeCrashed(returncode=1)
        with pytest.raises(SubprocessError):
            raise ClaudeTimeout(idle_timeout_s=60.0)

    def test_caught_as_session_error(self) -> None:
        with pytest.raises(SessionError):
            raise OversizeFrame(advertised=2, limit=1)
        with pytest.raises(SessionError):
            raise MalformedFrame(reason="x")
        with pytest.raises(SessionError):
            raise ClientDisconnected()

    def test_caught_as_handshake_error(self) -> None:
        with pytest.raises(HandshakeError):
            raise HandshakeTimeout(timeout_s=10.0)
        with pytest.raises(HandshakeError):
            raise ServerKeyMismatch(expected_key=b"a" * 32, actual_key=b"b" * 32)
        with pytest.raises(HandshakeError):
            raise MalformedHandshake(reason="bad magic")

    def test_all_caught_as_wire_error(self) -> None:
        for exc_factory in (
            lambda: HandshakeTimeout(timeout_s=10.0),
            lambda: ServerKeyMismatch(expected_key=b"a" * 32, actual_key=b"b" * 32),
            lambda: MalformedHandshake(reason="x"),
            lambda: OversizeFrame(advertised=2, limit=1),
            lambda: MalformedFrame(reason="x"),
            lambda: ServerDisconnected(),
            lambda: ClientDisconnected(),
            lambda: ClaudeCrashed(returncode=1),
            lambda: ClaudeTimeout(idle_timeout_s=60.0),
            lambda: AtCapacity(current=1, limit=1),
            lambda: InternalError(),
        ):
            with pytest.raises(WireError):
                raise exc_factory()
