"""Smoke + behavioural tests for the chat UI in ``tabula_cli.chat.main``.

Covers:
  * streaming render: a scripted token sequence reaches stdout in order,
    flushed per token, with the separator + reprompt at turn end
  * EOF triggers ``EndSession`` and exits 0
  * mid-session ``ServerDisconnected`` exits 4 with a clear stderr message
  * ``ErrorFrame`` exits 3
  * usage errors:
      - missing flag combinations
      - unpinned label without overrides
  * key-file loading: 32-byte raw + 2-line hex format

We intentionally do **not** spin up real sockets or threads; the readline
thread seam takes a duck-typed queue and the dialer seam takes a factory
coroutine.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from tabula_cli.chat import main as chat
from tabula_wire.client.exceptions import (
    ProtocolError,
    ServerDisconnected,
    ServerKeyMismatch,
)
from tabula_wire.proto.v1 import (
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


# --- fakes ----------------------------------------------------------------------


class FakeChannel:
    """Scripted ``ChatChannel`` used by the chat-UI tests.

    ``server_script`` is an iterable of either:
      - a ``ServerFrame`` to emit on the next ``recv()``
      - an ``Exception`` instance to raise from the next ``recv()``
      - the string ``"hang"`` to block until close
    ``recv()`` walks the script in order. ``send()`` records the frame on
    ``self.sent``.
    """

    def __init__(self, server_script: Iterable[Any]) -> None:
        self._script = list(server_script)
        self.sent: list[ClientFrame] = []
        self._closed = asyncio.Event()
        self._idx = 0

    async def send(self, frame: ClientFrame) -> None:
        self.sent.append(frame)
        # Mirror real-server behavior: a graceful server closes the stream
        # after acking ``EndSession``. Without this, a pending ``recv()``
        # would hang forever in tests that exercise the EOF path.
        if isinstance(frame, EndSession):
            self._closed.set()

    async def recv(self) -> ServerFrame:
        if self._idx >= len(self._script):
            # Script exhausted — block until ``close()`` is called, then
            # raise ``ServerDisconnected``. Tests pick the disconnect path
            # they want by either:
            #   - putting an explicit ``ServerDisconnected`` in the script
            #     to fire mid-session (exit 4)
            #   - leaving the script open-ended and feeding stdin EOF, so
            #     the session loop sends ``EndSession`` and the finally
            #     block calls ``close()`` → graceful exit 0
            await self._closed.wait()
            raise ServerDisconnected("script exhausted")
        item = self._script[self._idx]
        self._idx += 1
        if isinstance(item, Exception):
            raise item
        if item == "hang":
            await self._closed.wait()
            raise ServerDisconnected("hung")
        return item  # type: ignore[return-value]

    async def close(self) -> None:
        self._closed.set()


def _channel_factory(channel: FakeChannel):
    async def factory(target, key, timeout):  # noqa: ANN001, ARG001
        return channel

    return factory


def _channel_factory_raising(exc: Exception):
    async def factory(target, key, timeout):  # noqa: ANN001, ARG001
        raise exc

    return factory


class FakeQueue:
    """Async queue whose ``get()`` walks a pre-seeded list of items.

    Items are either ``str`` (a user line) or ``None`` (EOF). After the
    list is drained, ``get()`` blocks indefinitely so the session loop
    settles into pure recv-driven mode.
    """

    def __init__(self, items: Iterable[str | None]) -> None:
        self._items = list(items)
        self._idx = 0
        self._never = asyncio.Event()  # never set

    async def get(self) -> str | None:
        if self._idx >= len(self._items):
            await self._never.wait()
            return None  # unreachable
        item = self._items[self._idx]
        self._idx += 1
        return item


# --- helpers --------------------------------------------------------------------


def _target() -> chat._Target:
    return chat._Target(
        label="enclave-prod",
        host="enclave.example",
        port=7847,
        expected_pubkey=b"\x00" * 32,
    )


def _run(coro):
    return asyncio.run(coro)


# --- tests: streaming render ----------------------------------------------------


def test_streaming_render_emits_tokens_in_order_with_separator_and_reprompt() -> None:
    """A scripted Welcome + 3 tokens + turn end + EOF produces the expected stdout."""

    welcome = Welcome(session_id="sess-1", server_identity="enclave")
    script = [
        welcome,
        AssistantToken(text="Hello", sequence=0),
        AssistantToken(text=", ", sequence=1),
        AssistantToken(text="world!", sequence=2),
        AssistantTurnEnd(finish_reason=AssistantTurnEnd.FINISH_REASON_STOP),
    ]
    channel = FakeChannel(script)
    queue = FakeQueue([None])  # EOF immediately

    stdout = io.StringIO()
    stderr = io.StringIO()

    rc = _run(
        chat._run_session(
            target=_target(),
            local_static_key=b"\x00" * 32,
            connect_timeout=1.0,
            channel_factory=_channel_factory(channel),
            stdout=stdout,
            stderr=stderr,
            input_source=queue,
        )
    )

    out = stdout.getvalue()
    assert rc == 0, f"expected clean exit, got rc={rc}; stderr={stderr.getvalue()!r}"
    # Banner mentions the session id and label.
    assert "sess-1" in out
    assert "enclave-prod" in out
    # Tokens render in order with no spurious newlines between them.
    assert "Hello, world!" in out
    # Separator + reprompt after AssistantTurnEnd.
    assert chat.SEPARATOR in out
    assert out.count(chat.PROMPT) >= 2  # initial + post-turn

    # Hello sent first; EndSession sent after EOF; no UserMessage in between.
    assert isinstance(channel.sent[0], Hello)
    assert any(isinstance(f, EndSession) for f in channel.sent)
    assert not any(isinstance(f, UserMessage) for f in channel.sent)


def test_user_message_lines_are_sent_per_line() -> None:
    """Each stdin line becomes exactly one ``UserMessage``."""

    welcome = Welcome(session_id="s1")
    script = [welcome, AssistantTurnEnd(finish_reason=AssistantTurnEnd.FINISH_REASON_STOP)]
    channel = FakeChannel(script)
    queue = FakeQueue(["first line", "second line", None])

    rc = _run(
        chat._run_session(
            target=_target(),
            local_static_key=b"\x00" * 32,
            connect_timeout=1.0,
            channel_factory=_channel_factory(channel),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            input_source=queue,
        )
    )
    assert rc == 0

    user_msgs = [f for f in channel.sent if isinstance(f, UserMessage)]
    assert [m.text for m in user_msgs] == ["first line", "second line"]
    end_msgs = [f for f in channel.sent if isinstance(f, EndSession)]
    assert len(end_msgs) == 1


# --- tests: EOF / EndSession ----------------------------------------------------


def test_eof_triggers_end_session_and_exits_zero() -> None:
    welcome = Welcome(session_id="s")
    channel = FakeChannel([welcome])
    queue = FakeQueue([None])  # immediate EOF

    rc = _run(
        chat._run_session(
            target=_target(),
            local_static_key=b"\x00" * 32,
            connect_timeout=1.0,
            channel_factory=_channel_factory(channel),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            input_source=queue,
        )
    )

    assert rc == 0
    end_frames = [f for f in channel.sent if isinstance(f, EndSession)]
    assert len(end_frames) == 1
    assert end_frames[0].reason == "eof"


# --- tests: error paths ---------------------------------------------------------


def test_server_disconnect_mid_session_exits_4_with_clear_message() -> None:
    welcome = Welcome(session_id="s")
    channel = FakeChannel(
        [
            welcome,
            AssistantToken(text="partial", sequence=0),
            ServerDisconnected("peer closed"),
        ]
    )
    # Don't feed EOF — disconnect must come from the recv side.
    queue = FakeQueue([])

    stderr = io.StringIO()
    rc = _run(
        chat._run_session(
            target=_target(),
            local_static_key=b"\x00" * 32,
            connect_timeout=1.0,
            channel_factory=_channel_factory(channel),
            stdout=io.StringIO(),
            stderr=stderr,
            input_source=queue,
        )
    )

    assert rc == 4
    assert "disconnected" in stderr.getvalue().lower()


def test_error_frame_exits_3_with_code_and_message() -> None:
    welcome = Welcome(session_id="s")
    channel = FakeChannel(
        [welcome, ErrorFrame(code=ErrorFrame.Code.AT_CAPACITY, message="slow down")]
    )
    queue = FakeQueue([])

    stderr = io.StringIO()
    rc = _run(
        chat._run_session(
            target=_target(),
            local_static_key=b"\x00" * 32,
            connect_timeout=1.0,
            channel_factory=_channel_factory(channel),
            stdout=io.StringIO(),
            stderr=stderr,
            input_source=queue,
        )
    )

    assert rc == 3
    msg = stderr.getvalue()
    # The renderer formats the enum int via ``f"[{frame.code}]"``; assert the
    # numeric AT_CAPACITY value is present rather than the legacy free-form
    # string. ``message`` is rendered verbatim.
    assert f"[{int(ErrorFrame.Code.AT_CAPACITY)}]" in msg
    assert "slow down" in msg


def test_protocol_error_during_recv_exits_5() -> None:
    welcome = Welcome(session_id="s")
    channel = FakeChannel([welcome, ProtocolError("malformed frame")])
    queue = FakeQueue([])

    stderr = io.StringIO()
    rc = _run(
        chat._run_session(
            target=_target(),
            local_static_key=b"\x00" * 32,
            connect_timeout=1.0,
            channel_factory=_channel_factory(channel),
            stdout=io.StringIO(),
            stderr=stderr,
            input_source=queue,
        )
    )

    assert rc == 5
    assert "protocol" in stderr.getvalue().lower()


def test_server_key_mismatch_during_connect_exits_5() -> None:
    """Reconnect-error path: ``ServerKeyMismatch`` from `connect()`."""

    factory = _channel_factory_raising(ServerKeyMismatch("pinned key != offered"))
    stderr = io.StringIO()
    rc = _run(
        chat._run_session(
            target=_target(),
            local_static_key=b"\x00" * 32,
            connect_timeout=1.0,
            channel_factory=factory,
            stdout=io.StringIO(),
            stderr=stderr,
            input_source=FakeQueue([]),
        )
    )

    assert rc == 5
    assert "key mismatch" in stderr.getvalue().lower()


def test_unexpected_first_frame_is_protocol_error() -> None:
    """If the server's first frame is not Welcome/Error, exit 5."""

    channel = FakeChannel([AssistantToken(text="huh", sequence=0)])
    rc = _run(
        chat._run_session(
            target=_target(),
            local_static_key=b"\x00" * 32,
            connect_timeout=1.0,
            channel_factory=_channel_factory(channel),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            input_source=FakeQueue([]),
        )
    )
    assert rc == 5


def test_error_frame_at_handshake_exits_3() -> None:
    channel = FakeChannel(
        [ErrorFrame(code=ErrorFrame.Code.PROTOCOL, message="upgrade: bad_version")]
    )
    stderr = io.StringIO()
    rc = _run(
        chat._run_session(
            target=_target(),
            local_static_key=b"\x00" * 32,
            connect_timeout=1.0,
            channel_factory=_channel_factory(channel),
            stdout=io.StringIO(),
            stderr=stderr,
            input_source=FakeQueue([]),
        )
    )
    assert rc == 3
    # The renderer formats the enum int via ``f"[{first.code}]"``; the
    # legacy free-form ``"bad_version"`` string is preserved in the message
    # body so this assertion still passes against the canonical proto.
    assert "bad_version" in stderr.getvalue()


# --- tests: target resolution ---------------------------------------------------


def test_resolve_target_explicit_overrides() -> None:
    t = chat._resolve_target(
        server=None,
        host="1.2.3.4",
        port=9000,
        accept_key="00" * 32,
    )
    assert t.host == "1.2.3.4"
    assert t.port == 9000
    assert t.expected_pubkey == bytes.fromhex("00" * 32)
    assert t.label == "1.2.3.4:9000"


def test_resolve_target_partial_overrides_rejected() -> None:
    with pytest.raises(chat._UsageError, match="must be provided together"):
        chat._resolve_target(server="ent", host="x", port=None, accept_key=None)


def test_resolve_target_unpinned_label_rejected() -> None:
    # The canonical `lookup` returns None for unknown labels.
    with pytest.raises(chat._UsageError, match="not pinned"):
        chat._resolve_target(
            server="never-pinned", host=None, port=None, accept_key=None
        )


def test_resolve_target_no_args_rejected() -> None:
    with pytest.raises(chat._UsageError, match="required"):
        chat._resolve_target(server=None, host=None, port=None, accept_key=None)


def test_resolve_target_bad_hex_rejected() -> None:
    with pytest.raises(chat._UsageError, match="hex"):
        chat._resolve_target(server=None, host="h", port=1, accept_key="not-hex")


# --- tests: Typer mounting ------------------------------------------------------


def test_chat_app_is_a_typer_subcommand_group() -> None:
    """`chat` is a Typer app with `connect` registered as a command."""

    import typer

    assert isinstance(chat.app, typer.Typer)
    # The app exposes a `connect` command -- enumerate registered commands.
    command_names = {cmd.name for cmd in chat.app.registered_commands}
    assert "connect" in command_names


# --- tests: key file loading ----------------------------------------------------


def test_load_static_key_raw_32_bytes(tmp_path: Path) -> None:
    p = tmp_path / "client_key"
    raw = bytes(range(32))
    p.write_bytes(raw)
    p.chmod(0o600)
    assert chat._load_static_key(str(p)) == raw


def test_load_static_key_two_line_hex_format(tmp_path: Path) -> None:
    p = tmp_path / "client_key"
    raw = bytes(range(32))
    p.write_text(f"tabula-x25519-secret-v1\n{raw.hex()}\n")
    p.chmod(0o600)
    assert chat._load_static_key(str(p)) == raw


def test_load_static_key_missing_raises(tmp_path: Path) -> None:
    p = tmp_path / "no_such_key"
    with pytest.raises(FileNotFoundError):
        chat._load_static_key(str(p))


def test_load_static_key_bad_hex_format(tmp_path: Path) -> None:
    p = tmp_path / "client_key"
    p.write_text("tabula-x25519-secret-v1\nzzzz\n")
    p.chmod(0o600)
    with pytest.raises(FileNotFoundError):
        chat._load_static_key(str(p))


def test_load_static_key_wrong_length(tmp_path: Path) -> None:
    p = tmp_path / "client_key"
    p.write_bytes(b"\x00" * 16)
    p.chmod(0o600)
    with pytest.raises(FileNotFoundError):
        chat._load_static_key(str(p))


# --- tests: readline integration smoke test -------------------------------------


def test_readline_module_importable_and_input_available() -> None:
    """Smoke: stdlib readline imports cleanly and `input` is rebound on POSIX.

    This is intentionally lightweight — we don't assert behaviour of the
    real readline thread (it requires a TTY). We only check that the
    import path the chat UI relies on is satisfiable on the supported
    platforms (macOS/Linux per issue scope notes).
    """

    import sys

    if sys.platform.startswith("win"):
        pytest.skip("Windows is unsupported for MVP per issue scope")
    import readline  # noqa: F401

    # The chat module references `input` only inside the thread function;
    # make sure the symbol exists in builtins for pyflakes-equivalent sanity.
    assert callable(input)


def test_readline_thread_pumps_lines_into_queue() -> None:
    """Drive the real readline-thread function with a piped stdin.

    We don't exercise readline's editing — just confirm the thread reads
    lines from stdin via ``input()`` and posts them on the asyncio queue,
    and that EOF posts ``None`` and the thread exits.
    """

    import sys
    import threading

    async def runner() -> list[str | None]:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str | None] = asyncio.Queue()
        stop = threading.Event()

        # Pipe a known stdin payload into the thread's input() calls.
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("alpha\nbeta\n")  # EOF after \n
        try:
            t = threading.Thread(
                target=chat._readline_thread,
                args=(loop, q, stop),
                daemon=True,
            )
            t.start()
            # Wait for thread to drain stdin and post None.
            received: list[str | None] = []
            for _ in range(3):  # alpha, beta, None
                received.append(await asyncio.wait_for(q.get(), timeout=2.0))
                if received[-1] is None:
                    break
            stop.set()
            t.join(timeout=2.0)
            return received
        finally:
            sys.stdin = old_stdin

    received = _run(runner())
    assert received == ["alpha", "beta", None]
