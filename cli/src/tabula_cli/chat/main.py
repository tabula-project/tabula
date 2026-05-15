"""`tabula chat` Typer subcommand group with `connect` as the primary command.

Architecture:
    - one asyncio event loop runs the wire I/O
    - a dedicated thread reads stdin via :mod:`readline` (line-buffered, with
      basic editing/history) and pushes each line onto a :class:`asyncio.Queue`
    - the main loop multiplexes between (a) input queue -> ``UserMessage`` and
      (b) ``ChatChannel.recv()`` -> stdout/stderr render
    - tokens are written to stdout with ``flush=True`` per token (no print()
      overhead, no newline buffering); ``AssistantTurnEnd`` emits a newline,
      a subtle separator, and re-prompts

Sibling-issue boundaries:
    - the dialer (#27/#56) is invoked through :func:`tabula_wire.client.dialer.connect`;
      tests inject a fake :class:`ChatChannel` so this module is exercised
      without a real socket
    - the pinning store (#32/#54) is consulted via
      :func:`tabula_wire.client.pinning.lookup`; ``--host`` / ``--port`` /
      ``--accept-key`` provide an explicit override path
    - the typed exception hierarchy (#36/#54/#56) lives in
      :mod:`tabula_wire.client.exceptions`
    - the wire frames (#16/#45) live in :mod:`tabula_wire.proto.v1`

This module is mounted on the canonical Typer ``app`` in
:mod:`tabula_cli.main` (owned by #57's canonical layout). The mount looks like:

    from tabula_cli.chat.main import app as chat_app
    app.add_typer(chat_app, name="chat")
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from tabula_wire.client.dialer import ChatChannel, connect
from tabula_wire.client.exceptions import (
    ClientError,
    ConnectError,
    ProtocolError,
    ServerDisconnected,
    ServerKeyMismatch,
)
from tabula_wire.client.pinning import lookup
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

# Help-text default. The actual path is resolved via
# `tabula_cli.keygen.commands.default_key_path("client")` when the operator
# does NOT pass --key-path; that helper honors XDG_CONFIG_HOME (matching
# what `tabula keygen` writes to). Hardcoding `~/.config/tabula/client_key`
# here previously caused CI flake when XDG_CONFIG_HOME was set on the host
# but HOME was overridden by the test (issue #124).
_DEFAULT_KEY_PATH_SENTINEL = "<default>"
DEFAULT_KEY_PATH = "~/.config/tabula/client_key"  # display-only string
DEFAULT_PORT = 7847
PROMPT = "tabula> "
SEPARATOR = "\n───\n"  # newline + horizontal box-drawing line + newline
_PROTOCOL_VERSION = 1


# --- Typer subcommand group -----------------------------------------------------

app = typer.Typer(
    name="chat",
    help="Chat client commands.",
    no_args_is_help=True,
)


@app.command("connect")
def connect_command(
    server: str | None = typer.Argument(
        None,
        help="Pinned server label (resolves host/port/expected pubkey).",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        help="Override host (requires --port and --accept-key).",
    ),
    port: int | None = typer.Option(
        None,
        "--port",
        help="Override port (requires --host).",
    ),
    accept_key: str | None = typer.Option(
        None,
        "--accept-key",
        help=(
            "Hex-encoded server static pubkey to trust for this connection "
            "(required when --host is used and the label is not pinned)."
        ),
    ),
    key_path: str = typer.Option(
        _DEFAULT_KEY_PATH_SENTINEL,
        "--key-path",
        help=(
            f"Client static key file (default: {DEFAULT_KEY_PATH}; honors "
            "XDG_CONFIG_HOME, matching where `tabula keygen` writes)."
        ),
    ),
    connect_timeout: float = typer.Option(
        10.0,
        "--connect-timeout",
        help="Seconds to wait for TCP + handshake (default: 10.0).",
    ),
) -> None:
    """Open a chat session against a Tabula enclave.

    Resolves ``<server>`` through the pinning store unless
    ``--host`` / ``--port`` / ``--accept-key`` are provided.
    """

    rc = _run_connect(
        server=server,
        host=host,
        port=port,
        accept_key=accept_key,
        key_path=key_path,
        connect_timeout=connect_timeout,
    )
    raise typer.Exit(code=rc)


def _run_connect(
    *,
    server: str | None,
    host: str | None,
    port: int | None,
    accept_key: str | None,
    key_path: str,
    connect_timeout: float,
) -> int:
    """Resolve target, load key, open channel, run UI."""

    try:
        target = _resolve_target(
            server=server, host=host, port=port, accept_key=accept_key
        )
    except _UsageError as exc:
        print(f"tabula chat connect: {exc}", file=sys.stderr)
        return 2

    # Resolve the default key path via the same helper `tabula keygen`
    # uses, so XDG_CONFIG_HOME semantics match (issue #124).
    if key_path == _DEFAULT_KEY_PATH_SENTINEL:
        from tabula_cli.keygen.commands import default_key_path
        key_path = str(default_key_path("client"))

    try:
        local_static_key = _load_static_key(key_path)
    except FileNotFoundError as exc:
        print(f"tabula chat connect: {exc}", file=sys.stderr)
        return 2

    return asyncio.run(
        _run_session(
            target=target,
            local_static_key=local_static_key,
            connect_timeout=connect_timeout,
            channel_factory=None,  # production path uses tabula_wire.client.dialer.connect
            stdout=sys.stdout,
            stderr=sys.stderr,
            input_source=None,  # production path reads real stdin via readline
        )
    )


# --- target resolution & key loading -------------------------------------------


@dataclass(frozen=True)
class _Target:
    label: str
    host: str
    port: int
    expected_pubkey: bytes


class _UsageError(Exception):
    """CLI-level usage error reported to the user with exit code 2."""


def _resolve_target(
    *,
    server: str | None,
    host: str | None,
    port: int | None,
    accept_key: str | None,
) -> _Target:
    """Pick the connection target from CLI flags + pinning store.

    Three valid combinations:
      1. ``server`` only                -> look up label in pinning store
      2. ``--host --port --accept-key`` -> ad-hoc connection (label optional)
      3. ``server`` + overrides         -> label is the display name; overrides win
    """

    has_host = host is not None
    has_port = port is not None
    has_key = accept_key is not None

    # Explicit override path: all three of host/port/accept-key required together.
    if has_host or has_port or has_key:
        if not (has_host and has_port and has_key):
            raise _UsageError(
                "--host, --port, and --accept-key must be provided together"
            )
        try:
            pubkey = bytes.fromhex(accept_key)  # type: ignore[arg-type]
        except ValueError as exc:
            raise _UsageError(f"--accept-key must be hex-encoded: {exc}") from exc
        label = server or f"{host}:{port}"
        return _Target(
            label=label,
            host=host,  # type: ignore[arg-type]
            port=port,  # type: ignore[arg-type]
            expected_pubkey=pubkey,
        )

    # Pinning-only path: server label required.
    if not server:
        raise _UsageError(
            "either <server> or --host/--port/--accept-key is required"
        )
    entry = lookup(server)
    if entry is None:
        raise _UsageError(
            f"server label {server!r} is not pinned; "
            "add it via `tabula servers add` or use "
            "--host/--port/--accept-key"
        )
    return _Target(
        label=entry.label,
        host=entry.host,
        port=entry.port,
        expected_pubkey=entry.pubkey,
    )


def _load_static_key(path_str: str) -> bytes:
    """Load the client static-key bytes from disk.

    Accepts either of the two formats described in #31/#46:
      - raw 32-byte file
      - 2-line text format: header line ``tabula-x25519-secret-v1`` + 64 hex chars

    The chat UI does not validate the key cryptographically -- that happens
    inside the Noise handshake (#27/#56). We only enforce ``stat`` mode 0600
    advisory and the file shape so misconfiguration fails fast.
    """

    path = Path(path_str).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"client static key not found at {path}; run `tabula keygen`"
        )

    # Permission warning (advisory; #31/#46 owns the canonical check).
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0
    if mode and mode & 0o077:
        print(
            f"tabula chat connect: warning: {path} has permissive mode {mode:o}; "
            "expected 0600",
            file=sys.stderr,
        )

    raw = path.read_bytes()
    if len(raw) == 32:
        return raw

    # 2-line text format
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FileNotFoundError(
            f"client static key at {path} is not 32 raw bytes and not ASCII text"
        ) from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and lines[0] == "tabula-x25519-secret-v1":
        try:
            decoded = bytes.fromhex(lines[1])
        except ValueError as exc:
            raise FileNotFoundError(
                f"client static key at {path}: hex decode failed: {exc}"
            ) from exc
        if len(decoded) != 32:
            raise FileNotFoundError(
                f"client static key at {path}: expected 32 bytes, got {len(decoded)}"
            )
        return decoded
    raise FileNotFoundError(
        f"client static key at {path}: unrecognized format "
        "(expected 32 raw bytes or 'tabula-x25519-secret-v1' header + hex)"
    )


# --- session loop ---------------------------------------------------------------


# Type alias for a channel factory used by tests to inject a fake channel.
_ChannelFactory = Callable[[_Target, bytes, float], "asyncio.Future[ChatChannel]"]


async def _run_session(
    *,
    target: _Target,
    local_static_key: bytes,
    connect_timeout: float,
    channel_factory: Any | None,
    stdout: Any,
    stderr: Any,
    input_source: Any | None,
) -> int:
    """Open the channel, run the UI, return process exit code.

    ``channel_factory`` and ``input_source`` are the seams used by tests:
      - ``channel_factory(target, key, timeout) -> ChatChannel`` (awaitable)
      - ``input_source`` is a queue-like object with an awaitable ``get()``
        method that yields user lines or ``None`` for EOF; if ``None`` is
        passed for ``input_source`` we spin up a real readline thread
    """

    # Open channel.
    try:
        if channel_factory is None:
            from tabula_wire.crypto.keys import SecretKey

            sk = (
                local_static_key
                if isinstance(local_static_key, SecretKey)
                else SecretKey(raw=bytes(local_static_key))
            )
            channel = await connect(
                target.host,
                target.port,
                sk,
                target.expected_pubkey,
                connect_timeout_s=connect_timeout,
            )
        else:
            channel = await channel_factory(target, local_static_key, connect_timeout)
    except ServerKeyMismatch as exc:
        print(
            f"tabula chat connect: server key mismatch for {target.label!r}: {exc}",
            file=stderr,
        )
        return 5
    except ConnectError as exc:
        print(
            f"tabula chat connect: connection failed: "
            f"{type(exc).__name__}: {exc}",
            file=stderr,
        )
        return 5
    except ClientError as exc:
        print(
            f"tabula chat connect: handshake failed: "
            f"{type(exc).__name__}: {exc}",
            file=stderr,
        )
        return 5

    # Stdin source: real readline thread, or test-supplied queue.
    if input_source is None:
        input_queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stop_input = threading.Event()
        input_thread = threading.Thread(
            target=_readline_thread,
            args=(loop, input_queue, stop_input),
            daemon=True,
        )
        input_thread.start()
    else:
        input_queue = input_source
        stop_input = None
        input_thread = None

    try:
        return await _session_loop(
            channel=channel,
            input_queue=input_queue,
            stdout=stdout,
            stderr=stderr,
            session_label=target.label,
        )
    finally:
        if stop_input is not None:
            stop_input.set()
        await channel.close()


def _readline_thread(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[str | None],
    stop: threading.Event,
) -> None:
    """Stdin reader using stdlib :mod:`readline` for line editing + history.

    Posts each line (without trailing newline) onto ``queue``. Posts
    ``None`` on EOF. Exits when ``stop`` is set or stdin closes.
    """

    # Importing readline rebinds input() to use it on macOS/Linux. Windows
    # support is explicitly out of MVP scope per the issue scope notes.
    try:  # pragma: no cover -- platform-dependent
        import readline  # noqa: F401
    except ImportError:
        pass

    while not stop.is_set():
        try:
            line = input()
        except EOFError:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)
            return
        except KeyboardInterrupt:
            # Ctrl-C is handled by the main loop's signal handling. Bail.
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)
            return
        asyncio.run_coroutine_threadsafe(queue.put(line), loop)


async def _session_loop(
    *,
    channel: ChatChannel,
    input_queue: Any,  # asyncio.Queue[str | None] but we accept duck-typed fakes
    stdout: Any,
    stderr: Any,
    session_label: str,
) -> int:
    """Multiplex stdin -> UserMessage and channel.recv() -> render.

    Returns:
        0  on clean EOF + ``EndSession`` ack
        3  on ``ErrorFrame``
        4  on mid-session ``ServerDisconnected``
        5  on ``ProtocolError``
       130 on Ctrl-C
    """

    # 1. Hello / Welcome handshake.
    await channel.send(ClientFrame(hello=Hello(protocol_version=_PROTOCOL_VERSION)))
    try:
        first = await channel.recv()
    except ServerDisconnected as exc:
        print(
            f"tabula chat connect: server disconnected before Welcome: {exc}",
            file=stderr,
        )
        return 4
    except ProtocolError as exc:
        print(f"tabula chat connect: protocol error: {exc}", file=stderr)
        return 5
    # The dialer returns ServerFrame envelopes; unwrap via the oneof.
    which = first.WhichOneof("payload")
    if which == "error":
        print(
            f"tabula chat connect: server rejected hello "
            f"[{first.error.code}]: {first.error.message}",
            file=stderr,
        )
        return 3
    if which != "welcome":
        print(
            "tabula chat connect: protocol error: expected Welcome, "
            f"got payload={which!r}",
            file=stderr,
        )
        return 5

    # 2. Connection banner.
    _print_banner(stdout, session_label, first.welcome)
    _write_prompt(stdout)

    # 3. Multiplex loop.
    recv_task: asyncio.Task[Any] | None = None
    input_task: asyncio.Task[Any] | None = None
    eof_seen = False
    try:
        while True:
            if recv_task is None:
                recv_task = asyncio.create_task(channel.recv())
            if input_task is None and not eof_seen:
                input_task = asyncio.create_task(input_queue.get())

            wait_set = {t for t in (recv_task, input_task) if t is not None}
            if not wait_set:
                # Stdin EOF observed and no recv outstanding: send EndSession.
                await channel.send(ClientFrame(end=EndSession(reason="eof")))
                return 0
            done, _ = await asyncio.wait(
                wait_set, return_when=asyncio.FIRST_COMPLETED
            )

            # Handle stdin first so we send EndSession promptly on EOF.
            if input_task is not None and input_task in done:
                line = input_task.result()
                input_task = None
                if line is None:
                    eof_seen = True
                    await channel.send(ClientFrame(end=EndSession(reason="eof")))
                    # Continue draining recv until the server closes.
                else:
                    await channel.send(ClientFrame(user_message=UserMessage(text=line)))

            if recv_task is not None and recv_task in done:
                try:
                    frame = recv_task.result()
                except ServerDisconnected:
                    if eof_seen:
                        # Graceful close after EndSession.
                        return 0
                    print(
                        "\ntabula chat connect: server disconnected mid-session",
                        file=stderr,
                    )
                    return 4
                except ProtocolError as exc:
                    print(
                        f"\ntabula chat connect: protocol error: {exc}",
                        file=stderr,
                    )
                    return 5
                recv_task = None

                exit_code = _render_server_frame(frame, stdout, stderr)
                if exit_code is not None:
                    return exit_code
    except asyncio.CancelledError:
        # Caller (Ctrl-C handler) cancelled us. Best-effort EndSession.
        try:
            await channel.send(ClientFrame(end=EndSession(reason="interrupt")))
        except Exception:
            pass
        return 130
    finally:
        for t in (recv_task, input_task):
            if t is not None and not t.done():
                t.cancel()


# --- rendering ------------------------------------------------------------------


def _print_banner(stdout: Any, label: str, welcome: Welcome) -> None:
    banner = (
        f"connected to {label}  "
        f"(session {welcome.session_id}"
        + (f", {welcome.server_identity}" if welcome.server_identity else "")
        + ")\n"
        "type a message and press enter; Ctrl-D to end\n"
    )
    stdout.write(banner)
    stdout.flush()


def _write_prompt(stdout: Any) -> None:
    stdout.write(PROMPT)
    stdout.flush()


def _render_server_frame(frame: Any, stdout: Any, stderr: Any) -> int | None:
    """Render one ``ServerFrame``. Returns exit code or ``None`` to keep going."""

    which = frame.WhichOneof("payload")
    if which == "token":
        # Streaming render: write the chunk verbatim, flush per token.
        stdout.write(frame.token.text)
        stdout.flush()
        return None
    if which == "turn_end":
        stdout.write(SEPARATOR)
        stdout.flush()
        _write_prompt(stdout)
        return None
    if which == "error":
        # Newline first so we don't smear into a half-rendered token line.
        stdout.write("\n")
        stdout.flush()
        print(
            f"tabula chat connect: server error [{frame.error.code}]: {frame.error.message}",
            file=stderr,
        )
        return 3
    # Unknown / out-of-order payload.
    stdout.write("\n")
    stdout.flush()
    print(
        f"tabula chat connect: protocol error: unexpected payload={which!r} mid-session",
        file=stderr,
    )
    return 5


# --- module export --------------------------------------------------------------

__all__ = [
    "app",
    "DEFAULT_KEY_PATH",
    "DEFAULT_PORT",
    "PROMPT",
    "SEPARATOR",
]
