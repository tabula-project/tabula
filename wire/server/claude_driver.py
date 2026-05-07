"""ClaudeProcess — async subprocess driver for the `claude` CLI.

This module spawns a `claude` CLI subprocess in non-interactive streaming mode,
pipes prompts to its stdin, streams tokens from its stdout, and manages the
process lifecycle (kill on disconnect, detect crash). It is transport-agnostic:
it knows nothing about Noise, TCP, or protobuf. The session manager (#25)
consumes this driver.

Invocation
----------

The driver invokes `claude` in **streaming JSON mode**:

    claude --print --verbose \\
           --input-format stream-json --output-format stream-json \\
           --include-partial-messages

Why this combination:

- ``--print`` (``-p``)               non-interactive print-and-exit shape; no TTY.
- ``--input-format stream-json``     real-time streaming input over stdin: each
                                     line is a JSON object describing one user
                                     message. This is what makes multi-turn
                                     within a single process invocation
                                     possible — each subsequent line on stdin
                                     becomes the next user turn.
- ``--output-format stream-json``    real-time streaming output over stdout:
                                     each line is a JSON event (system init,
                                     assistant chunks, message stop, result).
- ``--include-partial-messages``     surface token-level deltas, not just
                                     completed messages, so the streaming UX
                                     feels live.
- ``--verbose``                      required by ``claude`` when both input and
                                     output are stream-json + ``--print``;
                                     emits richer event metadata.

Multi-turn over a single subprocess: **YES, supported.**

The ``claude`` CLI's combination of ``--input-format=stream-json`` with
``--output-format=stream-json`` is explicitly designed for an interactive,
turn-by-turn conversation driven by an external process — exactly what Epic
#13 wants ("Server spawns one ``claude`` per session, kills on disconnect").
The Builder verified this against ``claude --help`` (see PR description for
the open-question resolution). One subprocess per chat session is therefore
the model used here.

Wire shape
----------

Input on stdin (one JSON object per line, newline-terminated)::

    {"type": "user", "message": {"role": "user", "content": "hello"}}

Output on stdout (one JSON object per line). The events relevant to this
driver are:

    - ``{"type": "system", "subtype": "init", ...}``    emitted once at start
    - ``{"type": "assistant", "message": {...}}``        full assistant message
    - ``{"type": "stream_event", "event": {...}}``       partial token deltas
                                                         (``content_block_delta``)
    - ``{"type": "result", "subtype": "...", ...}``      end-of-turn marker

The driver yields token chunks (text deltas) from
``stream_event.event.delta.text`` and treats a ``result`` event as the
turn-end signal. Completed ``assistant`` messages without preceding deltas
are also yielded as a single chunk so the driver works even if
``--include-partial-messages`` is unsupported by an older CLI build.

Environment
-----------

The driver does not configure auth. The session manager / deployment is
responsible for setting (in the inherited environment):

    - ``ANTHROPIC_VERTEX_PROJECT_ID``     GCP project for Vertex AI
    - ``CLOUD_ML_REGION``                 Vertex region (e.g. ``us-east5``)
    - ``CLAUDE_CODE_USE_VERTEX``          ``"1"`` to route via Vertex
    - Standard ADC env (``GOOGLE_APPLICATION_CREDENTIALS`` or VM service
      account)

The driver only takes ``cwd`` (working directory for ``claude``) and an
optional ``env`` override. If ``env`` is ``None`` the parent process
environment is inherited.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# Default invocation. The session manager (#25) may override the binary or
# add flags via the ``argv_override`` constructor argument.
DEFAULT_CLAUDE_ARGV: Final[tuple[str, ...]] = (
    "claude",
    "--print",
    "--verbose",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--include-partial-messages",
)

# How much stderr we keep around for crash diagnostics. The acceptance
# criteria say "last 1 KiB of stderr".
_STDERR_TAIL_BYTES: Final[int] = 1024


class ClaudeProcessError(Exception):
    """Base class for ClaudeProcess errors."""


class ClaudeProcessNotStarted(ClaudeProcessError):
    """Raised when an operation requires the process to have been started."""


class ClaudeProcessCrashed(ClaudeProcessError):
    """Raised by ``stream_tokens`` when the subprocess exits unexpectedly.

    Attributes:
        returncode: process exit code (may be ``None`` if unknown).
        stderr_tail: last ``_STDERR_TAIL_BYTES`` of stderr as text (``""`` if
                     none was captured).
    """

    def __init__(self, returncode: int | None, stderr_tail: str) -> None:
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        super().__init__(
            f"claude subprocess exited unexpectedly "
            f"(returncode={returncode!r}); stderr tail: {stderr_tail!r}"
        )


class ClaudeProcess:
    """Async driver around a single ``claude`` CLI subprocess.

    Lifecycle::

        proc = ClaudeProcess()
        await proc.start(cwd=Path("/repo"))
        await proc.send_prompt("hello")
        async for chunk in proc.stream_tokens():
            ...
        reason = await proc.end_turn()    # "stop"
        await proc.send_prompt("follow-up")    # multi-turn within same process
        ...
        await proc.kill()

    The driver is **not** thread-safe; it is async-task-safe but assumes a
    single producer (the session manager) drives prompts and a single consumer
    pulls tokens.
    """

    def __init__(
        self,
        argv_override: tuple[str, ...] | None = None,
    ) -> None:
        self._argv: tuple[str, ...] = argv_override or DEFAULT_CLAUDE_ARGV
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_tail: deque[bytes] = deque(maxlen=_STDERR_TAIL_BYTES)
        self._stderr_pump_task: asyncio.Task[None] | None = None
        # Parsed events from stdout, fed to ``stream_tokens`` / ``end_turn``.
        self._event_queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._stdout_pump_task: asyncio.Task[None] | None = None
        # Set when the subprocess has exited (cleanly or otherwise) and the
        # queue has been sealed with a sentinel.
        self._exited = asyncio.Event()
        # Latched crash info — set by the stdout pump if exit was unexpected.
        self._crash_returncode: int | None = None
        self._crash_signaled = False
        # Per-turn finish-reason latch. Set by stream_tokens when it sees a
        # result/turn-end event; consumed by end_turn.
        self._turn_finish: asyncio.Future[str] | None = None
        self._kill_called = False

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        """True if the subprocess is running."""
        if self._proc is None:
            return False
        return self._proc.returncode is None

    async def start(
        self,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> None:
        """Spawn the ``claude`` subprocess.

        Args:
            cwd: working directory passed to the subprocess. Must exist.
            env: optional environment override. If ``None`` the parent process
                 environment is inherited (recommended for Vertex AI ADC).

        Raises:
            ClaudeProcessError: if already started or spawn fails.
            FileNotFoundError: if the ``claude`` binary cannot be found.
        """
        if self._proc is not None:
            raise ClaudeProcessError("ClaudeProcess.start called twice")
        if not cwd.exists():
            raise FileNotFoundError(f"cwd does not exist: {cwd}")

        spawn_env = dict(env) if env is not None else dict(os.environ)

        logger.debug(
            "spawning claude: argv=%s cwd=%s env_keys=%s",
            self._argv,
            cwd,
            sorted(spawn_env.keys()),
        )

        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            cwd=str(cwd),
            env=spawn_env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Start stderr drain so it never blocks the subprocess writing.
        self._stderr_pump_task = asyncio.create_task(
            self._pump_stderr(), name="claude-stderr-pump"
        )
        # Start stdout pump that parses JSONL events and seals the queue on exit.
        self._stdout_pump_task = asyncio.create_task(
            self._pump_stdout(), name="claude-stdout-pump"
        )

    async def send_prompt(self, text: str) -> None:
        """Write a user prompt to stdin in the stream-json input format.

        The line written is one JSON object terminated by ``\\n``::

            {"type":"user","message":{"role":"user","content":"<text>"}}

        This format is required by ``claude --input-format=stream-json``.

        Raises:
            ClaudeProcessNotStarted: if ``start`` has not been called.
            ClaudeProcessCrashed: if stdin is closed because the subprocess
                                  exited.
        """
        if self._proc is None or self._proc.stdin is None:
            raise ClaudeProcessNotStarted("send_prompt before start")
        if self._proc.returncode is not None:
            raise ClaudeProcessCrashed(
                self._proc.returncode, self._stderr_tail_text()
            )

        # Reset the per-turn finish latch so end_turn() will await the *next*
        # result event, not a stale one.
        self._turn_finish = asyncio.get_running_loop().create_future()

        line = (
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": text},
                    # Some claude builds want a parent_tool_use_id slot; include
                    # an explicit None so the schema is unambiguous.
                    "parent_tool_use_id": None,
                    "session_id": _stable_session_id(self),
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        try:
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ClaudeProcessCrashed(
                self._proc.returncode, self._stderr_tail_text()
            ) from exc

    async def stream_tokens(self) -> AsyncIterator[str]:
        """Yield assistant text chunks for the current turn.

        The iterator terminates when the subprocess emits a ``result`` event
        (turn end). The finish reason is latched and can then be read with
        ``end_turn``.

        Raises:
            ClaudeProcessNotStarted: if ``start`` has not been called.
            ClaudeProcessCrashed: if the subprocess exits unexpectedly mid-turn.
        """
        if self._proc is None:
            raise ClaudeProcessNotStarted("stream_tokens before start")

        while True:
            event = await self._event_queue.get()
            if event is None:
                # Sentinel — pump signaled subprocess exit.
                if self._crash_returncode is not None or self._crash_signaled:
                    raise ClaudeProcessCrashed(
                        self._proc.returncode, self._stderr_tail_text()
                    )
                # Normal-ish EOF (e.g., process exited 0 without a result event).
                # Treat as crash — for a chat session this is unexpected.
                raise ClaudeProcessCrashed(
                    self._proc.returncode, self._stderr_tail_text()
                )

            etype = event.get("type")

            if etype == "stream_event":
                # Partial messages: claude emits Anthropic-shaped event objects.
                inner = event.get("event") or {}
                inner_type = inner.get("type")
                if inner_type == "content_block_delta":
                    delta = inner.get("delta") or {}
                    text = delta.get("text")
                    if text:
                        yield text
                # Other inner types (message_start, content_block_start, etc.)
                # are ignored — they're metadata.
                continue

            if etype == "assistant":
                # Whole-message form (when partials are unavailable). Yield the
                # text blocks so callers still see output even without
                # --include-partial-messages support.
                msg = event.get("message") or {}
                content = msg.get("content") or []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if text:
                            yield text
                continue

            if etype == "result":
                # End of turn. Latch the finish reason and stop iterating.
                reason = (
                    event.get("subtype")
                    or event.get("stop_reason")
                    or "stop"
                )
                if self._turn_finish is not None and not self._turn_finish.done():
                    self._turn_finish.set_result(str(reason))
                return

            # Unknown / unhandled event types — log and continue.
            logger.debug("ignoring claude event: %s", etype)

    async def end_turn(self) -> str:
        """Return the finish reason for the most recent turn.

        Must be called after ``stream_tokens`` has been driven to completion
        for the current turn. Returns the ``result.subtype`` (e.g.
        ``"success"``, ``"error_max_turns"``) when available, falling back to
        ``"stop"``.

        Raises:
            ClaudeProcessNotStarted: if ``start`` has not been called or if no
                                     prompt has been sent on this turn.
        """
        if self._proc is None:
            raise ClaudeProcessNotStarted("end_turn before start")
        if self._turn_finish is None:
            raise ClaudeProcessNotStarted("end_turn before send_prompt")
        return await self._turn_finish

    async def kill(self) -> int | None:
        """Terminate the subprocess and drain pipes. Idempotent.

        Returns the exit code (negative for a signal). Safe to call multiple
        times — subsequent calls return the cached exit code without raising.
        """
        if self._proc is None:
            return None

        self._kill_called = True

        # 1. Politely close stdin so ``claude`` can flush.
        if self._proc.stdin is not None and not self._proc.stdin.is_closing():
            with contextlib.suppress(Exception):
                self._proc.stdin.close()

        # 2. If the process is still alive, send SIGTERM, then SIGKILL on
        #    timeout. We're idempotent — if already exited, skip.
        if self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self._proc.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)

        # 3. Wait for pumps to finish so we don't leak tasks.
        for task in (self._stdout_pump_task, self._stderr_pump_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        # 4. If a turn was in flight, wake any waiting end_turn() with a
        #    synthesized "killed" reason so callers don't hang. (Always
        #    "killed" here — only kill() reaches this branch.)
        if self._turn_finish is not None and not self._turn_finish.done():
            self._turn_finish.set_result("killed")

        return self._proc.returncode

    # ------------------------------------------------------------------
    # internal pumps
    # ------------------------------------------------------------------

    async def _pump_stdout(self) -> None:
        """Read stdout JSONL, push parsed events to the queue.

        On EOF / exit, push ``None`` as a sentinel and set the crash latch if
        the exit was not user-requested (kill).
        """
        assert self._proc is not None
        stdout = self._proc.stdout
        assert stdout is not None
        try:
            while True:
                raw = await stdout.readline()
                if not raw:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "non-JSON line on claude stdout: %r", line[:200]
                    )
                    continue
                if not isinstance(event, dict):
                    logger.warning("non-object claude event: %r", event)
                    continue
                await self._event_queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("error in claude stdout pump")
        finally:
            # Wait for process exit so we know the returncode at sentinel time.
            with contextlib.suppress(Exception):
                await self._proc.wait()
            rc = self._proc.returncode
            if not self._kill_called and (rc is None or rc != 0):
                self._crash_returncode = rc
                self._crash_signaled = True
            self._exited.set()
            await self._event_queue.put(None)

    async def _pump_stderr(self) -> None:
        """Drain stderr into a tail buffer for crash diagnostics."""
        assert self._proc is not None
        stderr = self._proc.stderr
        assert stderr is not None
        try:
            while True:
                chunk = await stderr.read(4096)
                if not chunk:
                    break
                # Append one byte at a time so the deque maxlen acts as a
                # rolling tail. (Cheap; stderr is low-volume.)
                for b in chunk:
                    self._stderr_tail.append(bytes((b,)))
                # Also surface stderr to the logger at debug level.
                with contextlib.suppress(UnicodeDecodeError):
                    logger.debug("claude stderr: %s", chunk.decode("utf-8", "replace").rstrip())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("error in claude stderr pump")

    def _stderr_tail_text(self) -> str:
        return b"".join(self._stderr_tail).decode("utf-8", errors="replace")


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


# Per-instance stable session id. claude wants a uuid-shaped session id in
# stream-json input messages (used for resume/replay); it does NOT need to
# match anything in our system — one per ClaudeProcess instance is fine.
def _stable_session_id(proc: ClaudeProcess) -> str:
    sid = getattr(proc, "_session_id", None)
    if sid is None:
        sid = str(uuid.uuid4())
        # Stash on the instance so all turns share it.
        proc._session_id = sid  # type: ignore[attr-defined]
    return sid


__all__ = [
    "ClaudeProcess",
    "ClaudeProcessError",
    "ClaudeProcessNotStarted",
    "ClaudeProcessCrashed",
    "DEFAULT_CLAUDE_ARGV",
]
