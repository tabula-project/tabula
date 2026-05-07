"""Unit tests for ``wire.server.claude_driver.ClaudeProcess``.

The tests use ``wire/server/tests/fixtures/fake_claude.sh`` as a stand-in for
the real ``claude`` CLI. The fixture's mode is selected by setting the
``FAKE_CLAUDE_MODE`` environment variable; behaviors are documented inside
the fixture script.

The driver is exercised against the fixture by overriding its argv via
``ClaudeProcess(argv_override=...)`` so no monkey-patching of subprocess is
needed and no real ``claude`` binary is required.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from claude_driver import (
    ClaudeProcess,
    ClaudeProcessCrashed,
    ClaudeProcessNotStarted,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fake_claude.sh"


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _argv() -> tuple[str, ...]:
    """argv that runs the fixture as if it were ``claude``."""
    return (str(FIXTURE),)


def _env(mode: str) -> dict[str, str]:
    """Test env: select fixture mode, but inherit PATH so /usr/bin/env can
    resolve ``bash`` and ``python3`` for the fixture."""
    return {
        "FAKE_CLAUDE_MODE": mode,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
    }


async def _drain(it: AsyncIterator[str]) -> str:
    """Consume an async iterator of str chunks and join them."""
    parts: list[str] = []
    async for chunk in it:
        parts.append(chunk)
    return "".join(parts)


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


@pytest.fixture()
def cwd(tmp_path: Path) -> Path:
    return tmp_path


# ----------------------------------------------------------------------
# happy path
# ----------------------------------------------------------------------


async def test_happy_path_single_turn(cwd: Path) -> None:
    """Send 1 prompt, observe streamed response, observe turn end."""
    proc = ClaudeProcess(argv_override=_argv())
    await proc.start(cwd=cwd, env=_env("echo"))
    try:
        assert proc.is_alive

        await proc.send_prompt("hello")
        text = await _drain(proc.stream_tokens())
        reason = await proc.end_turn()

        assert text == "echo: hello"
        assert reason == "success"
    finally:
        await proc.kill()


# ----------------------------------------------------------------------
# multi-turn (driver supports it; epic's "one subprocess per session" model)
# ----------------------------------------------------------------------


async def test_multi_turn_one_process(cwd: Path) -> None:
    """Two prompts in one process, each with its own streamed response."""
    proc = ClaudeProcess(argv_override=_argv())
    await proc.start(cwd=cwd, env=_env("multiturn"))
    try:
        await proc.send_prompt("alpha")
        text1 = await _drain(proc.stream_tokens())
        reason1 = await proc.end_turn()

        await proc.send_prompt("beta")
        text2 = await _drain(proc.stream_tokens())
        reason2 = await proc.end_turn()

        assert text1 == "turn1:alpha"
        assert text2 == "turn2:beta"
        assert reason1 == "success"
        assert reason2 == "success"
        assert proc.is_alive
    finally:
        await proc.kill()


# ----------------------------------------------------------------------
# crash path
# ----------------------------------------------------------------------


async def test_crash_mid_stream_raises(cwd: Path) -> None:
    """Subprocess exits non-zero mid-stream → ClaudeProcessCrashed."""
    proc = ClaudeProcess(argv_override=_argv())
    await proc.start(cwd=cwd, env=_env("crash"))
    try:
        await proc.send_prompt("hello")
        with pytest.raises(ClaudeProcessCrashed) as exc_info:
            async for _chunk in proc.stream_tokens():
                # The fixture emits one delta then dies; we may consume
                # zero or one chunks before the crash propagates.
                pass

        # Returncode + stderr tail surfaced.
        assert exc_info.value.returncode == 7
        assert "simulated crash" in exc_info.value.stderr_tail
    finally:
        await proc.kill()


# ----------------------------------------------------------------------
# kill path
# ----------------------------------------------------------------------


async def test_kill_terminates_long_running(cwd: Path) -> None:
    """Kill a long-running fixture cleanly; exit code is surfaced."""
    proc = ClaudeProcess(argv_override=_argv())
    await proc.start(cwd=cwd, env=_env("sleep"))
    assert proc.is_alive

    rc = await proc.kill()
    assert not proc.is_alive
    # SIGTERM gives 143 in our trap, or the platform may report it as -15;
    # accept either signed or unsigned encoding plus our trap exit code.
    assert rc in (143, -15, -2, 130)


async def test_kill_is_idempotent(cwd: Path) -> None:
    """kill() may be called multiple times without raising."""
    proc = ClaudeProcess(argv_override=_argv())
    await proc.start(cwd=cwd, env=_env("sleep"))

    rc1 = await proc.kill()
    rc2 = await proc.kill()
    rc3 = await proc.kill()
    assert rc1 == rc2 == rc3
    assert not proc.is_alive


async def test_kill_wakes_pending_end_turn(cwd: Path) -> None:
    """If a turn was in flight, kill() unblocks any pending end_turn caller."""
    proc = ClaudeProcess(argv_override=_argv())
    await proc.start(cwd=cwd, env=_env("sleep"))

    await proc.send_prompt("hello")
    # send_prompt latches a turn-finish future. Issue a kill in parallel and
    # await end_turn — it must resolve, not hang.
    kill_task = asyncio.create_task(proc.kill())
    reason = await asyncio.wait_for(proc.end_turn(), timeout=5.0)
    await kill_task

    assert reason == "killed"


# ----------------------------------------------------------------------
# robustness: non-JSON stdout lines
# ----------------------------------------------------------------------


async def test_non_json_stdout_lines_ignored(cwd: Path) -> None:
    """Driver tolerates stray non-JSON lines on stdout."""
    proc = ClaudeProcess(argv_override=_argv())
    await proc.start(
        cwd=cwd, env=_env("bad_json")
    )
    try:
        await proc.send_prompt("hi")
        text = await _drain(proc.stream_tokens())
        reason = await proc.end_turn()

        assert text == "ok"
        assert reason == "success"
    finally:
        await proc.kill()


# ----------------------------------------------------------------------
# guard rails / API misuse
# ----------------------------------------------------------------------


async def test_send_prompt_before_start_raises() -> None:
    proc = ClaudeProcess(argv_override=_argv())
    with pytest.raises(ClaudeProcessNotStarted):
        await proc.send_prompt("nope")


async def test_end_turn_before_send_prompt_raises(cwd: Path) -> None:
    proc = ClaudeProcess(argv_override=_argv())
    await proc.start(cwd=cwd, env=_env("echo"))
    try:
        with pytest.raises(ClaudeProcessNotStarted):
            await proc.end_turn()
    finally:
        await proc.kill()


async def test_double_start_raises(cwd: Path) -> None:
    proc = ClaudeProcess(argv_override=_argv())
    await proc.start(cwd=cwd, env=_env("sleep"))
    try:
        with pytest.raises(Exception):
            await proc.start(cwd=cwd)
    finally:
        await proc.kill()


async def test_start_with_missing_cwd_raises(tmp_path: Path) -> None:
    proc = ClaudeProcess(argv_override=_argv())
    with pytest.raises(FileNotFoundError):
        await proc.start(cwd=tmp_path / "does-not-exist")


# ----------------------------------------------------------------------
# is_alive transitions
# ----------------------------------------------------------------------


async def test_is_alive_false_before_start_and_after_kill(cwd: Path) -> None:
    proc = ClaudeProcess(argv_override=_argv())
    assert proc.is_alive is False
    await proc.start(cwd=cwd, env=_env("sleep"))
    assert proc.is_alive is True
    await proc.kill()
    assert proc.is_alive is False
