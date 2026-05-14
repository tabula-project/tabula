"""``python -m tabula_wire.server`` — minimal CLI for running the chat server.

Usage::

    python -m tabula_wire.server \\
        --host 127.0.0.1 --port 7847 \\
        --static-key ~/.config/tabula/server_key \\
        --claude-cmd claude \\
        --cwd /tmp/tabula-state

The arguments are the minimum needed to bind the listener + spawn one
``claude`` subprocess per session. For production use, run the server
under a process supervisor (systemd, launchd) with the appropriate
environment for the chosen claude backend (Vertex ADC, Anthropic API
token, etc.) — this entrypoint does nothing magical with auth.

Test fixtures pass a path to ``wire/tests/server/fixtures/fake_claude.sh``
via ``--claude-cmd`` for offline integration testing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from tabula_wire.crypto.keys import load_secret_key
from tabula_wire.server.claude_driver import ClaudeProcess
from tabula_wire.server.config import (
    DEFAULT_MAX_CONCURRENT_SESSIONS,
    DEFAULT_SERVER_VERSION,
    PROTOCOL_VERSION,
    ServerConfig,
)
from tabula_wire.server.main import serve_with_sessions


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tabula_wire.server",
        description="Run a Tabula chat server (Noise XX + protobuf + claude subprocess).",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Default 127.0.0.1 (loopback). Use 0.0.0.0 for an enclave.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=7847,
        help="Bind port. Default 7847 (Tabula chat).",
    )
    p.add_argument(
        "--static-key",
        required=True,
        help=(
            "Path to the server's static X25519 secret-key file in canonical "
            "tabula format (see tabula_wire.crypto.keys). Generate with "
            "`tabula keygen --role server --out <path>`."
        ),
    )
    p.add_argument(
        "--claude-cmd",
        default="claude",
        help=(
            "Argv[0] for the per-session claude subprocess. May be the bare "
            "binary name (resolved via PATH) or an absolute path. Defaults to "
            "'claude'. Test fixtures pass the path to fake_claude.sh."
        ),
    )
    p.add_argument(
        "--cwd",
        default=None,
        help=(
            "Working directory for claude subprocesses. Defaults to the "
            "directory containing --static-key, then the current process cwd."
        ),
    )
    p.add_argument(
        "--max-concurrent-sessions",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_SESSIONS,
        help=f"Concurrent session cap. Default {DEFAULT_MAX_CONCURRENT_SESSIONS}.",
    )
    p.add_argument(
        "--server-version",
        default=DEFAULT_SERVER_VERSION,
        help=f"Server identity string echoed in Welcome. Default '{DEFAULT_SERVER_VERSION}'.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level. Default INFO.",
    )
    return p


async def _amain(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    secret = load_secret_key(args.static_key)
    static_key = bytes(secret.raw)

    cwd = (
        Path(args.cwd).expanduser().resolve()
        if args.cwd
        else Path(args.static_key).expanduser().resolve().parent
    )
    cwd.mkdir(parents=True, exist_ok=True)

    claude_argv = (args.claude_cmd,)

    async def claude_factory(config: ServerConfig) -> ClaudeProcess:
        proc = ClaudeProcess(argv_override=claude_argv)
        await proc.start(cwd=config.cwd)
        return proc

    config = ServerConfig(
        cwd=cwd,
        max_concurrent_sessions=args.max_concurrent_sessions,
        protocol_version=PROTOCOL_VERSION,
        server_version=args.server_version,
    )

    server = await serve_with_sessions(
        host=args.host,
        port=args.port,
        static_key=static_key,
        claude_factory=claude_factory,
        config=config,
    )
    print(
        f"Tabula server listening on {args.host}:{args.port} "
        f"(pubkey {secret.public_key().hex()})",
        flush=True,
    )

    stop = asyncio.Event()

    def _shutdown(*_a):
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    await stop.wait()
    server.close()
    await server.wait_closed()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
