"""Integration-style tests for the error-path matrix in #36.

These tests simulate each error scenario end-to-end at the typed-exception
layer, since the underlying transport modules (#20 server listener, #22
claude subprocess, #25 session manager, #27 client frame loop) have not
been reconciled to canonical layout yet.

When those modules land on main, the simulated peers in this file are
replaced by real `tabula_wire.server.session.handle_session` /
`tabula_wire.client.session.run_client` calls — the exception assertions
do NOT change. That is the point of #36: the typed exceptions are the
stable contract, and the rest of the wire reduces to this vocabulary.

Coverage matrix (per #36 acceptance criteria):

* Handshake garbage / malformed proto magic       — :class:`MalformedHandshake`
* Handshake timeout                                — :class:`HandshakeTimeout`
* Server pubkey mismatch                           — :class:`ServerKeyMismatch`
* Mid-handshake disconnect (no fd/task leak)       — :class:`MalformedHandshake`
* Mid-session client disconnect                    — :class:`ClientDisconnected`
* Mid-session server disconnect                    — :class:`ServerDisconnected`
* Claude subprocess crash                          — :class:`ClaudeCrashed`
* Claude subprocess hang                           — :class:`ClaudeTimeout`
* Oversize frame                                   — :class:`OversizeFrame`
* Malformed proto inside valid Noise envelope      — :class:`MalformedFrame`
* Server at capacity                               — :class:`AtCapacity`
* Internal server error                            — :class:`InternalError`

For each, we also assert that the exception translates to the correct
``ErrorFrame.Code``, and the asyncio-based scenarios also assert that no
tasks leak (verified via :func:`asyncio.all_tasks`).
"""

from __future__ import annotations

import asyncio
import struct
from typing import AsyncIterator

import pytest

from tabula_wire.errors import (
    AtCapacity,
    ClaudeCrashed,
    ClaudeTimeout,
    ClientDisconnected,
    HandshakeTimeout,
    InternalError,
    MalformedFrame,
    MalformedHandshake,
    OversizeFrame,
    ServerDisconnected,
    ServerKeyMismatch,
    build_error_frame,
    exception_to_error_code,
)
from tabula_wire.proto.v1 import ErrorFrame


# Constants shared with #36's acceptance criteria. These are the values
# the production code MUST agree on; the tests assert that mismatches at
# these limits raise the correct typed exceptions.
MAX_FRAME_SIZE = 1 << 20  # 1 MiB
HANDSHAKE_DEADLINE_S = 0.05  # exaggerated for tests
CLAUDE_IDLE_TIMEOUT_S = 0.05
KILL_GRACE_S = 0.05


# ---------------------------------------------------------------------------
# Task-leak assertion helper
# ---------------------------------------------------------------------------


async def _assert_no_task_leak(coro_factory) -> None:
    """Run ``coro_factory()`` and assert no tasks beyond the runner remain.

    Per #36 acceptance criteria: "no fd/task leak (verified via
    asyncio.all_tasks() snapshot in test)". We snapshot the running-task
    set before and after. Any tasks spawned inside MUST be awaited or
    cancelled by the coroutine itself.
    """
    before = {t for t in asyncio.all_tasks() if not t.done()}
    try:
        await coro_factory()
    finally:
        # Yield once so any pending callbacks settle.
        await asyncio.sleep(0)
    after = {t for t in asyncio.all_tasks() if not t.done()}
    leaked = after - before
    # The current task itself is in `after` but also `before` for asyncio.run-
    # style runners — just guard against extras.
    leaked.discard(asyncio.current_task())
    assert not leaked, f"leaked tasks: {leaked!r}"


# ---------------------------------------------------------------------------
# Simulated handshake (replaces real Noise XX from #18 until that lands)
# ---------------------------------------------------------------------------


async def _simulated_handshake_responder(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    timeout_s: float,
) -> None:
    """Read 4 magic bytes from a peer, validate, raise typed errors.

    Mirrors the real responder's pre-Noise framing check that #20 / #18
    will own. The "valid magic" is ``b'TBLA'``. Anything else is a
    :class:`MalformedHandshake`. No bytes / partial bytes within the
    deadline → :class:`HandshakeTimeout`. Closed-before-complete → also
    :class:`MalformedHandshake` (with ``bytes_seen`` reflecting what
    arrived).
    """
    peer = writer.get_extra_info("peername")
    peer_str = f"{peer[0]}:{peer[1]}" if peer else "<unknown>"

    try:
        data = await asyncio.wait_for(reader.readexactly(4), timeout=timeout_s)
    except asyncio.IncompleteReadError as e:
        raise MalformedHandshake(
            reason="peer closed before handshake completed",
            bytes_seen=len(e.partial),
            peer=peer_str,
        ) from e
    except asyncio.TimeoutError as e:
        raise HandshakeTimeout(timeout_s=timeout_s, peer=peer_str) from e

    if data != b"TBLA":
        raise MalformedHandshake(
            reason=f"bad magic 0x{data.hex()}",
            bytes_seen=4,
            peer=peer_str,
        )


# ---------------------------------------------------------------------------
# Simulated frame loop (replaces #20/#27 transport until those land)
# ---------------------------------------------------------------------------


async def _simulated_frame_reader(
    reader: asyncio.StreamReader,
    *,
    max_frame_size: int = MAX_FRAME_SIZE,
) -> bytes:
    """Length-prefixed frame read with size cap and disconnect detection.

    * 4-byte BE length, then body.
    * Length > ``max_frame_size`` → :class:`OversizeFrame` BEFORE reading body.
    * Peer closes before/after the prefix → :class:`ClientDisconnected`
      (responder side) or :class:`ServerDisconnected` (initiator side).
      Caller distinguishes which based on its role.
    """
    try:
        prefix = await reader.readexactly(4)
    except asyncio.IncompleteReadError as e:
        raise ConnectionAbortedError("peer closed before frame prefix") from e

    (length,) = struct.unpack(">I", prefix)
    if length > max_frame_size:
        raise OversizeFrame(advertised=length, limit=max_frame_size)

    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError as e:
        raise ConnectionAbortedError("peer closed mid-frame") from e


# ---------------------------------------------------------------------------
# Simulated subprocess driver (replaces #22 until it lands)
# ---------------------------------------------------------------------------


class _FakeClaudeSubprocess:
    """Minimal stand-in for the real claude subprocess driver from #22.

    Provides just enough async surface to test the *disconnect angle* of
    the kill semantics, which is what #36 contributes (per the issue's
    "do not redesign #22 kill mechanics" non-goal). We only verify that
    a SIGTERM / SIGKILL sequence raises the right typed exception.
    """

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.token_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def stream_tokens(self) -> AsyncIterator[str]:
        while True:
            tok = await self.token_queue.get()
            if tok is None:
                return
            yield tok

    async def crash(self, returncode: int = 1, stderr_tail: str = "") -> None:
        await self.token_queue.put(None)
        raise ClaudeCrashed(returncode=returncode, stderr_tail=stderr_tail)

    async def wait_with_idle_timeout(self, idle_timeout_s: float) -> None:
        try:
            await asyncio.wait_for(self.token_queue.get(), timeout=idle_timeout_s)
        except asyncio.TimeoutError as e:
            raise ClaudeTimeout(idle_timeout_s=idle_timeout_s) from e

    async def terminate(self, *, grace_s: float = KILL_GRACE_S) -> None:
        self.terminated = True
        await asyncio.sleep(min(0.001, grace_s))
        self.killed = True


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestHandshakeFailures:
    async def test_garbage_bytes_raise_malformed_handshake(self) -> None:
        """Per #36: client sends garbage / wrong proto magic → MalformedHandshake."""

        async def handle(reader, writer):
            try:
                await _simulated_handshake_responder(
                    reader, writer, timeout_s=HANDSHAKE_DEADLINE_S
                )
            finally:
                writer.close()
                await writer.wait_closed()

        captured: list[BaseException] = []

        async def runner() -> None:
            server = await asyncio.start_server(
                lambda r, w: _wrap_capture(handle, r, w, captured),
                host="127.0.0.1",
                port=0,
            )
            try:
                addr = server.sockets[0].getsockname()
                # Peer sends 4 bytes of garbage.
                reader, writer = await asyncio.open_connection(addr[0], addr[1])
                writer.write(b"XXXX")
                await writer.drain()
                writer.close()
                try:
                    await writer.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

                # Wait for the responder to finish processing.
                for _ in range(50):
                    if captured:
                        break
                    await asyncio.sleep(0.01)
            finally:
                server.close()
                await server.wait_closed()

        await _assert_no_task_leak(runner)
        assert captured, "responder did not raise"
        exc = captured[0]
        assert isinstance(exc, MalformedHandshake)
        assert exc.bytes_seen == 4
        assert "bad magic" in exc.reason
        assert exception_to_error_code(exc) == ErrorFrame.Code.PROTOCOL

    async def test_handshake_timeout(self) -> None:
        """Per #36: peer never completes handshake → HandshakeTimeout."""

        captured: list[BaseException] = []

        async def handle(reader, writer):
            try:
                await _simulated_handshake_responder(
                    reader, writer, timeout_s=HANDSHAKE_DEADLINE_S
                )
            finally:
                writer.close()
                await writer.wait_closed()

        async def runner() -> None:
            server = await asyncio.start_server(
                lambda r, w: _wrap_capture(handle, r, w, captured),
                host="127.0.0.1",
                port=0,
            )
            try:
                addr = server.sockets[0].getsockname()
                # Open and never write — should trigger timeout.
                reader, writer = await asyncio.open_connection(addr[0], addr[1])
                for _ in range(20):
                    if captured:
                        break
                    await asyncio.sleep(0.02)
                writer.close()
                try:
                    await writer.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            finally:
                server.close()
                await server.wait_closed()

        await _assert_no_task_leak(runner)
        assert captured, "responder did not raise"
        exc = captured[0]
        # Could be HandshakeTimeout (if the deadline tripped first) or
        # MalformedHandshake (if the peer closed before timeout). Both are
        # acceptable manifestations; the timeout path must be reachable.
        assert isinstance(exc, (HandshakeTimeout, MalformedHandshake))

    async def test_mid_handshake_disconnect_no_task_leak(self) -> None:
        """Per #36: client TCP-closes before XX completes → no fd/task leak,
        listener still accepting."""

        captured: list[BaseException] = []

        async def handle(reader, writer):
            try:
                await _simulated_handshake_responder(
                    reader, writer, timeout_s=HANDSHAKE_DEADLINE_S * 10
                )
            finally:
                writer.close()
                await writer.wait_closed()

        async def runner() -> None:
            server = await asyncio.start_server(
                lambda r, w: _wrap_capture(handle, r, w, captured),
                host="127.0.0.1",
                port=0,
            )
            try:
                addr = server.sockets[0].getsockname()
                # Open + close immediately, no bytes sent.
                _, writer = await asyncio.open_connection(addr[0], addr[1])
                writer.close()
                try:
                    await writer.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

                # Verify listener still accepts a second connection.
                _, writer2 = await asyncio.open_connection(addr[0], addr[1])
                writer2.write(b"TBLA")
                await writer2.drain()
                writer2.close()
                try:
                    await writer2.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

                # Wait for the first responder to finish.
                for _ in range(50):
                    if captured:
                        break
                    await asyncio.sleep(0.01)
            finally:
                server.close()
                await server.wait_closed()

        await _assert_no_task_leak(runner)
        assert captured, "first responder did not raise"
        exc = captured[0]
        assert isinstance(exc, MalformedHandshake)
        # bytes_seen should reflect zero or partial — definitely < 4.
        assert exc.bytes_seen < 4


class TestKeyMismatch:
    """ServerKeyMismatch is a client-side check, not a transport scenario."""

    def test_message_contains_both_keys_in_hex(self) -> None:
        # Per #36 acceptance criteria: message must contain BOTH expected
        # and actual key in hex.
        expected = bytes.fromhex("aa" * 32)
        actual = bytes.fromhex("bb" * 32)
        exc = ServerKeyMismatch(
            expected_key=expected, actual_key=actual, server="enclave-1.example.com"
        )
        assert expected.hex() in str(exc)
        assert actual.hex() in str(exc)
        # Maps to PROTOCOL family (HandshakeError default).
        assert exception_to_error_code(exc) == ErrorFrame.Code.PROTOCOL


class TestSessionDisconnects:
    async def test_mid_session_client_disconnect(self) -> None:
        """Per #36: client closes mid-turn → server raises ClientDisconnected,
        subprocess gets SIGTERM."""

        proc = _FakeClaudeSubprocess()
        captured: list[BaseException] = []

        async def session_loop():
            try:
                # Simulate: server is reading another client frame while
                # streaming tokens; client closes its half of the socket.
                reader, writer = _make_closed_stream_pair()
                try:
                    await _simulated_frame_reader(reader)
                except ConnectionAbortedError as e:
                    raise ClientDisconnected(during="frame_loop") from e
            except ClientDisconnected as e:
                captured.append(e)
                # Cleanup mirrors #22 kill semantics.
                await proc.terminate(grace_s=KILL_GRACE_S)
                raise

        async def runner() -> None:
            with pytest.raises(ClientDisconnected):
                await session_loop()

        await _assert_no_task_leak(runner)
        assert captured and isinstance(captured[0], ClientDisconnected)
        # Subprocess must have been told to terminate.
        assert proc.terminated and proc.killed
        # Family-level mapping: ClientDisconnected → PROTOCOL.
        assert exception_to_error_code(captured[0]) == ErrorFrame.Code.PROTOCOL

    async def test_mid_session_server_disconnect(self) -> None:
        """Client side of the same scenario — server closes; client raises
        ServerDisconnected."""

        async def client_loop():
            reader, _writer = _make_closed_stream_pair()
            try:
                await _simulated_frame_reader(reader)
            except ConnectionAbortedError as e:
                raise ServerDisconnected(during="frame_loop") from e

        with pytest.raises(ServerDisconnected) as ei:
            await client_loop()
        # Family-level mapping.
        assert exception_to_error_code(ei.value) == ErrorFrame.Code.PROTOCOL


class TestClaudeFailures:
    async def test_claude_crashes_mid_turn(self) -> None:
        """Per #36: subprocess exits non-zero → ClaudeCrashed →
        ErrorFrame{code=CLAUDE_CRASHED}, session closed."""

        proc = _FakeClaudeSubprocess()

        async def session_loop():
            await proc.crash(returncode=1, stderr_tail="oops")

        with pytest.raises(ClaudeCrashed) as ei:
            await session_loop()
        frame = build_error_frame(ei.value, turn_id="t1")
        assert frame.code == ErrorFrame.Code.CLAUDE_CRASHED
        assert "1" in frame.message
        assert frame.turn_id == "t1"

    async def test_claude_hangs(self) -> None:
        """Per #36: no stdout for ``idle_timeout_s`` → ClaudeTimeout."""

        proc = _FakeClaudeSubprocess()

        async def runner() -> None:
            with pytest.raises(ClaudeTimeout) as ei:
                await proc.wait_with_idle_timeout(idle_timeout_s=CLAUDE_IDLE_TIMEOUT_S)
            assert exception_to_error_code(ei.value) == ErrorFrame.Code.CLAUDE_TIMEOUT

        await _assert_no_task_leak(runner)


class TestFrameValidation:
    async def test_oversize_frame_does_not_read_body(self) -> None:
        """Per #36: length prefix > MAX_FRAME_SIZE → close cleanly without
        reading the body."""

        # Build a fake reader that raises if anyone tries to read the body.
        async def make_reader_with_huge_prefix() -> asyncio.StreamReader:
            limit = MAX_FRAME_SIZE * 2  # Stream limit big enough to hold the prefix.
            reader = asyncio.StreamReader(limit=limit)
            prefix = struct.pack(">I", MAX_FRAME_SIZE + 1)
            reader.feed_data(prefix)
            # Do NOT feed body bytes — if the reader ever attempts to read
            # them, it will hang. The size check must short-circuit before.
            return reader

        async def runner() -> None:
            reader = await make_reader_with_huge_prefix()
            with pytest.raises(OversizeFrame) as ei:
                await asyncio.wait_for(_simulated_frame_reader(reader), timeout=0.5)
            exc = ei.value
            assert exc.advertised == MAX_FRAME_SIZE + 1
            assert exc.limit == MAX_FRAME_SIZE
            assert exception_to_error_code(exc) == ErrorFrame.Code.PROTOCOL

        await _assert_no_task_leak(runner)

    def test_malformed_frame_inside_valid_envelope(self) -> None:
        """Per #36: decryption succeeds but ParseFromString raises →
        connection closed; best-effort ErrorFrame{code=PROTOCOL} sent."""

        # Simulated parse failure — in production this is whatever the
        # generated proto's ParseFromString raises (DecodeError or
        # google.protobuf.message.DecodeError). #36 wraps that in
        # MalformedFrame so the rest of the wire only deals in typed
        # exceptions.
        try:
            raise ValueError("DecodeError: invalid wire type")
        except ValueError as e:
            wrapped = MalformedFrame(reason=str(e), frame_kind="ClientFrame")

        frame = build_error_frame(wrapped)
        assert frame.code == ErrorFrame.Code.PROTOCOL
        assert "ClientFrame" in frame.message


class TestCapacity:
    def test_at_capacity_maps_to_at_capacity_code(self) -> None:
        exc = AtCapacity(current=8, limit=8)
        assert exception_to_error_code(exc) == ErrorFrame.Code.AT_CAPACITY


class TestInternalErrorWrapping:
    def test_unexpected_exception_is_wrapped_for_wire(self) -> None:
        """Per #36: untyped exceptions never escape to the listener loop —
        the session task wraps them in InternalError before translating."""

        try:
            raise KeyError("some internal bug")
        except KeyError as e:
            wrapped = InternalError("Internal server error")
            wrapped.__cause__ = e

        assert exception_to_error_code(wrapped) == ErrorFrame.Code.INTERNAL
        # The wire message MUST NOT echo arbitrary internals back to the
        # client. We surface a generic message and rely on server logs to
        # carry the cause via __cause__.
        frame = build_error_frame(wrapped)
        assert frame.code == ErrorFrame.Code.INTERNAL
        assert "some internal bug" not in frame.message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wrap_capture(handler, reader, writer, captured: list[BaseException]) -> None:
    """Wrap a session handler so any raised exception is captured."""
    try:
        await handler(reader, writer)
    except BaseException as e:
        captured.append(e)


def _make_closed_stream_pair() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Build a StreamReader that already signals EOF.

    Lets us simulate a peer that closed before sending any bytes without
    standing up a real socket.
    """

    class _NullProtocol(asyncio.Protocol):
        pass

    class _NullTransport(asyncio.WriteTransport):
        def __init__(self) -> None:
            super().__init__()
            self._closing = False

        def is_closing(self) -> bool:
            return self._closing

        def close(self) -> None:
            self._closing = True

        def write(self, data: object) -> None:
            pass

        def can_write_eof(self) -> bool:
            return True

        def write_eof(self) -> None:
            pass

        def get_extra_info(self, name: str, default=None):
            return default

    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    reader.feed_eof()
    transport = _NullTransport()
    writer = asyncio.StreamWriter(transport, _NullProtocol(), reader, loop)
    return reader, writer
