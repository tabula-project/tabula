"""Tests for the session manager (issue #25).

Covered acceptance criteria:

- Hello → Welcome → user messages → streamed tokens → EndSession
- First frame not Hello → ErrorFrame{PROTOCOL} + close
- Protocol version mismatch → ErrorFrame{PROTOCOL} + close
- Subprocess crash mid-turn → ErrorFrame{CLAUDE_CRASHED} + tear down
- Capacity exceeded → ErrorFrame{AT_CAPACITY} + close (no claude spawned)
- Client disconnect mid-turn → subprocess killed, no leak
- Cancellation propagates: cancelling the session task kills the subprocess
- Cleanup is exception-safe (subprocess always killed)
- Sequence numbers reset per turn
- Send-side transport failure doesn't leak the subprocess
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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
from tabula_wire.server.config import PROTOCOL_VERSION, ServerConfig
from tabula_wire.server.main import handle_connection
from tabula_wire.server.session import handle_session

from tests.server.conftest import (
    FakeClaudeProcess,
    FrameChannel,
    make_factory,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _config(max_concurrent: int = 4) -> ServerConfig:
    return ServerConfig(
        cwd=Path("/tmp"),
        max_concurrent_sessions=max_concurrent,
        protocol_version=PROTOCOL_VERSION,
        server_version="tabula-server/test",
    )


def _hello(version: int = PROTOCOL_VERSION) -> ClientFrame:
    return ClientFrame(hello=Hello(protocol_version=version))


def _user(text: str) -> ClientFrame:
    return ClientFrame(user_message=UserMessage(text=text))


def _end() -> ClientFrame:
    return ClientFrame(end_session=EndSession())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_two_turns_and_end_session() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()
    proc.queue_turn("hello", " world")
    proc.queue_turn("a", "b", "c")

    await chan.push(_hello())
    await chan.push(_user("first"))
    await chan.push(_user("second"))
    await chan.push(_end())

    await handle_session(
        handshake_info={"peer": "test"},
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    out = chan.outbox

    # Welcome first
    assert isinstance(out[0].payload(), Welcome)
    welcome = out[0].welcome
    assert welcome is not None
    assert welcome.server_version == "tabula-server/test"
    assert welcome.session_id  # non-empty UUID

    # Turn 1: 2 tokens then turn end
    assert out[1].assistant_token == AssistantToken(text="hello", sequence=0)
    assert out[2].assistant_token == AssistantToken(text=" world", sequence=1)
    assert out[3].assistant_turn_end == AssistantTurnEnd(
        finish_reason=FinishReason.STOP
    )

    # Turn 2: 3 tokens then turn end (sequence resets)
    assert out[4].assistant_token == AssistantToken(text="a", sequence=0)
    assert out[5].assistant_token == AssistantToken(text="b", sequence=1)
    assert out[6].assistant_token == AssistantToken(text="c", sequence=2)
    assert out[7].assistant_turn_end == AssistantTurnEnd(
        finish_reason=FinishReason.STOP
    )
    assert len(out) == 8

    # Driver received both prompts in order
    assert proc.prompts_received == ["first", "second"]

    # Subprocess was killed during cleanup
    assert proc.kill_count == 1
    assert proc.is_alive is False


@pytest.mark.asyncio
async def test_sequence_numbers_reset_each_turn() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()
    proc.queue_turn("x")
    proc.queue_turn("y")

    await chan.push(_hello())
    await chan.push(_user("p1"))
    await chan.push(_user("p2"))
    await chan.push(_end())

    await handle_session(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    tokens = [
        f.assistant_token
        for f in chan.outbox
        if f.assistant_token is not None
    ]
    assert [t.sequence for t in tokens] == [0, 0]


# ---------------------------------------------------------------------------
# Protocol violations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_frame_not_hello_returns_protocol_error() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()  # never used

    await chan.push(_user("malicious"))
    # No need to push more — session manager returns after the error.

    await handle_session(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    assert len(chan.outbox) == 1
    err = chan.outbox[0].error
    assert err is not None
    assert err.code == ErrorCode.PROTOCOL
    # Driver factory should not have been invoked
    assert proc.prompts_received == []
    assert proc.kill_count == 0


@pytest.mark.asyncio
async def test_protocol_version_mismatch_rejected() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()  # never used

    await chan.push(_hello(version=PROTOCOL_VERSION + 99))

    await handle_session(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    assert len(chan.outbox) == 1
    err = chan.outbox[0].error
    assert err is not None
    assert err.code == ErrorCode.PROTOCOL
    assert "protocol_version" in err.message
    assert proc.kill_count == 0


@pytest.mark.asyncio
async def test_unexpected_mid_session_frame_returns_protocol_error() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()
    proc.queue_turn("ok")

    await chan.push(_hello())
    # Send a second Hello after the handshake — unexpected.
    await chan.push(_hello())

    await handle_session(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    # Welcome, then ErrorFrame, then nothing.
    out = chan.outbox
    assert isinstance(out[0].payload(), Welcome)
    err = out[1].error
    assert err is not None
    assert err.code == ErrorCode.PROTOCOL

    # Subprocess was spawned (after Hello) and then killed in cleanup.
    assert proc.kill_count == 1


# ---------------------------------------------------------------------------
# Subprocess crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subprocess_crash_midturn_emits_error_and_tears_down() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()
    proc.queue_crash("partial-1", "partial-2", exit_code=42)

    await chan.push(_hello())
    await chan.push(_user("hi"))
    # Push EndSession too — we want to be sure the manager doesn't continue
    # after the crash. If the manager incorrectly continued, it would try to
    # consume EndSession; instead it should return after the crash.
    await chan.push(_end())

    await handle_session(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    out = chan.outbox
    # Welcome, partial-1, partial-2, then ErrorFrame{CLAUDE_CRASHED}.
    assert isinstance(out[0].payload(), Welcome)
    assert out[1].assistant_token == AssistantToken(text="partial-1", sequence=0)
    assert out[2].assistant_token == AssistantToken(text="partial-2", sequence=1)
    err = out[-1].error
    assert err is not None
    assert err.code == ErrorCode.CLAUDE_CRASHED
    # No AssistantTurnEnd was sent — the turn ended in error.
    assert all(f.assistant_turn_end is None for f in out)

    assert proc.kill_count == 1


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_connection_rejects_over_capacity() -> None:
    cfg = _config(max_concurrent=1)
    sem = asyncio.Semaphore(cfg.max_concurrent_sessions)

    # Pre-acquire the only slot to simulate one session already running.
    await sem.acquire()

    chan = FrameChannel()
    proc = FakeClaudeProcess()  # must not be touched
    await chan.push(_hello())

    await handle_connection(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=cfg,
        claude_factory=make_factory(proc),
        semaphore=sem,
    )

    assert len(chan.outbox) == 1
    err = chan.outbox[0].error
    assert err is not None
    assert err.code == ErrorCode.AT_CAPACITY
    # Factory was never called → fake's prompt list / kill count are 0.
    assert proc.prompts_received == []
    assert proc.kill_count == 0


@pytest.mark.asyncio
async def test_handle_connection_acquires_and_releases_capacity() -> None:
    cfg = _config(max_concurrent=2)
    sem = asyncio.Semaphore(cfg.max_concurrent_sessions)

    chan = FrameChannel()
    proc = FakeClaudeProcess()
    proc.queue_turn("ok")
    await chan.push(_hello())
    await chan.push(_user("p"))
    await chan.push(_end())

    await handle_connection(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=cfg,
        claude_factory=make_factory(proc),
        semaphore=sem,
    )

    # Slot was released after handle_session returned.
    assert sem.locked() is False
    # We should be able to acquire all slots again.
    for _ in range(cfg.max_concurrent_sessions):
        await sem.acquire()
    assert sem.locked() is True


# ---------------------------------------------------------------------------
# Disconnect / cancellation / cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_disconnect_midturn_kills_subprocess() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()
    proc.queue_turn("a", "b", "c", "d")

    await chan.push(_hello())
    await chan.push(_user("p"))
    # Don't send EndSession; just close the transport while tokens stream.
    await chan.close()

    await handle_session(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    # All four tokens + turn end must have been delivered before EOF read.
    tokens = [
        f.assistant_token
        for f in chan.outbox
        if f.assistant_token is not None
    ]
    assert [t.text for t in tokens] == ["a", "b", "c", "d"]
    assert proc.kill_count == 1


@pytest.mark.asyncio
async def test_disconnect_before_hello_no_subprocess_spawned() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()  # must not be touched

    await chan.close()  # immediate EOF

    await handle_session(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    assert chan.outbox == []
    assert proc.kill_count == 0


@pytest.mark.asyncio
async def test_cancellation_propagates_and_kills_subprocess() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()
    # Long stream so we can cancel mid-turn.
    proc.queue_turn(*[f"tok{i}" for i in range(1000)])

    await chan.push(_hello())
    await chan.push(_user("hang"))

    task = asyncio.create_task(
        handle_session(
            handshake_info=None,
            recv_frames=chan.recv_frames(),
            send_frame=chan.send_frame,
            config=_config(),
            claude_factory=make_factory(proc),
        )
    )

    # Let the session boot and start streaming a few tokens.
    for _ in range(20):
        await asyncio.sleep(0)
    assert any(f.welcome for f in chan.outbox)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The finally block must have killed the subprocess even though we
    # cancelled the task.
    assert proc.kill_count == 1
    assert proc.is_alive is False


@pytest.mark.asyncio
async def test_cleanup_runs_when_send_fails_after_welcome() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()
    proc.queue_turn("a", "b")

    await chan.push(_hello())
    await chan.push(_user("p"))

    # First send (Welcome) succeeds; arm a failure for any subsequent send.
    # We arm it *after* pushing frames so we can flip the switch only when
    # we want failures to begin. The simplest reliable way: subclass send.
    welcome_seen = asyncio.Event()
    real_send = chan.send_frame

    async def flaky_send(frame: ServerFrame) -> None:
        if frame.welcome is not None:
            await real_send(frame)
            welcome_seen.set()
            return
        raise ConnectionResetError("simulated transport drop")

    # Should NOT raise out of handle_session: internal errors are swallowed
    # (best-effort error frame send is tolerated to fail silently).
    await handle_session(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=flaky_send,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    # The subprocess must still have been killed.
    assert proc.kill_count == 1


@pytest.mark.asyncio
async def test_kill_called_exactly_once_on_normal_completion() -> None:
    chan = FrameChannel()
    proc = FakeClaudeProcess()
    proc.queue_turn("only")

    await chan.push(_hello())
    await chan.push(_user("hi"))
    await chan.push(_end())

    await handle_session(
        handshake_info=None,
        recv_frames=chan.recv_frames(),
        send_frame=chan.send_frame,
        config=_config(),
        claude_factory=make_factory(proc),
    )

    assert proc.kill_count == 1
