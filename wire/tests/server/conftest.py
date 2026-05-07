"""Shared async test helpers.

This module exposes:

- :class:`FakeClaudeProcess`: an in-memory implementation of
  :class:`tabula_wire.server.session.ClaudeProcessProtocol`. It is scripted with
  per-turn token sequences (or a crash signal), satisfies the kill /
  is_alive contract, and tracks call history for assertions.
- :class:`FrameChannel`: a paired "transport" — an async iterator the
  session manager reads from and an awaitable callable it writes to —
  with a small inbox/outbox API for tests.

The fakes are intentionally minimal: no I/O, no threads, deterministic.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from tabula_wire.proto.v1 import ClientFrame, ServerFrame
from tabula_wire.server.session import ClaudeProcessCrashed, ClaudeProcessProtocol


# ---------------------------------------------------------------------------
# Fake ClaudeProcess
# ---------------------------------------------------------------------------


@dataclass
class _Turn:
    """Scripted output for one user turn.

    Exactly one of ``tokens`` or ``crash`` should be populated. ``tokens``
    is a list of strings yielded one at a time. ``crash`` causes the next
    ``stream_tokens`` call to raise :class:`ClaudeProcessCrashed` after
    the listed prefix tokens have been yielded.
    """

    tokens: list[str] = field(default_factory=list)
    crash_after: int | None = None  # if set, crash after yielding N tokens
    crash_exit_code: int = 1
    crash_stderr: str = "fake claude exploded"


class FakeClaudeProcess:
    """Scripted in-memory ``ClaudeProcessProtocol`` implementation."""

    def __init__(self, turns: list[_Turn] | None = None) -> None:
        self._turns: list[_Turn] = list(turns) if turns else []
        self._turn_index = 0
        self._alive = True
        self._kill_count = 0
        self._exit_code: int | None = None
        # Recorded call history for assertions
        self.prompts_received: list[str] = []
        # An event tests can await to be sure ``kill`` was called.
        self.kill_event = asyncio.Event()

    # ---- Scripting helpers ----

    def queue_turn(self, *tokens: str) -> None:
        """Add a happy-path turn yielding the given tokens."""
        self._turns.append(_Turn(tokens=list(tokens)))

    def queue_crash(
        self,
        *prefix_tokens: str,
        exit_code: int = 1,
        stderr: str = "fake claude exploded",
    ) -> None:
        """Add a turn that yields ``prefix_tokens`` then crashes."""
        self._turns.append(
            _Turn(
                tokens=list(prefix_tokens),
                crash_after=len(prefix_tokens),
                crash_exit_code=exit_code,
                crash_stderr=stderr,
            )
        )

    # ---- ClaudeProcessProtocol surface ----

    @property
    def is_alive(self) -> bool:
        return self._alive

    async def send_prompt(self, text: str) -> None:
        if not self._alive:
            raise RuntimeError("send_prompt on dead FakeClaudeProcess")
        self.prompts_received.append(text)

    async def stream_tokens(self) -> AsyncIterator[str]:
        if not self._alive:
            raise RuntimeError("stream_tokens on dead FakeClaudeProcess")
        if self._turn_index >= len(self._turns):
            raise AssertionError(
                "FakeClaudeProcess: no more scripted turns "
                f"(received turn #{self._turn_index + 1})"
            )
        turn = self._turns[self._turn_index]
        self._turn_index += 1

        for i, tok in enumerate(turn.tokens):
            # Cooperative yield so cancellation / interleaved ops work.
            await asyncio.sleep(0)
            yield tok
            if turn.crash_after is not None and i + 1 >= turn.crash_after:
                self._alive = False
                self._exit_code = turn.crash_exit_code
                raise ClaudeProcessCrashed(
                    "fake claude exploded",
                    exit_code=turn.crash_exit_code,
                    stderr_tail=turn.crash_stderr,
                )

    async def kill(self) -> int | None:
        self._kill_count += 1
        self._alive = False
        self.kill_event.set()
        if self._exit_code is None:
            self._exit_code = -9  # simulated SIGKILL
        return self._exit_code

    @property
    def kill_count(self) -> int:
        return self._kill_count


# Verify the fake satisfies the protocol at import time. Catches drift
# between the fake and the protocol immediately.
assert isinstance(FakeClaudeProcess(), ClaudeProcessProtocol)


# ---------------------------------------------------------------------------
# Frame channel
# ---------------------------------------------------------------------------


@dataclass
class _ChannelState:
    inbox: asyncio.Queue[ClientFrame | None]
    outbox: list[ServerFrame] = field(default_factory=list)
    send_failure: BaseException | None = None
    closed: bool = False


class FrameChannel:
    """In-memory paired transport for session tests.

    Use :meth:`push` to enqueue a ``ClientFrame`` for the session manager
    to read. Use :meth:`close` to signal EOF (raises StopAsyncIteration on
    the next read). Sent ``ServerFrame``s land in :attr:`outbox` in order.
    """

    def __init__(self) -> None:
        self._state = _ChannelState(inbox=asyncio.Queue())

    # ---- Test-side API ----

    async def push(self, frame: ClientFrame) -> None:
        await self._state.inbox.put(frame)

    async def close(self) -> None:
        """Signal EOF to the session's recv side."""
        self._state.closed = True
        await self._state.inbox.put(None)

    @property
    def outbox(self) -> list[ServerFrame]:
        return list(self._state.outbox)

    def fail_sends_with(self, exc: BaseException) -> None:
        """Make every subsequent ``send_frame`` call raise ``exc``."""
        self._state.send_failure = exc

    # ---- Session-side API ----

    def recv_frames(self) -> AsyncIterator[ClientFrame]:
        state = self._state

        async def _gen() -> AsyncIterator[ClientFrame]:
            while True:
                item = await state.inbox.get()
                if item is None:
                    return
                yield item

        return _gen()

    async def send_frame(self, frame: ServerFrame) -> None:
        if self._state.send_failure is not None:
            raise self._state.send_failure
        self._state.outbox.append(frame)


def make_factory(*procs: FakeClaudeProcess) -> Any:
    """Return a ``ClaudeFactory`` that hands out the given fakes in order.

    Calling more times than ``procs`` provided raises ``AssertionError``.
    """
    queue = list(procs)

    async def _factory(_config: Any) -> FakeClaudeProcess:
        if not queue:
            raise AssertionError(
                "ClaudeFactory called more times than fakes were provided"
            )
        return queue.pop(0)

    return _factory


__all__ = [
    "FakeClaudeProcess",
    "FrameChannel",
    "make_factory",
]
