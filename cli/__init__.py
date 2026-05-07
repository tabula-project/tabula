"""Top-level `tabula` CLI entrypoint.

This module owns the subcommand registry. Sub-issues that add new commands
(#31 `keygen`, #32 `servers`) register here. The chat UI (#29) lives in
:mod:`cli.chat`.

    tabula chat connect <server-name>
    tabula chat connect --host H --port P --accept-key HEX

Exit codes:
    0 — clean exit (EOF / EndSession ack)
    1 — generic failure (unhandled exception)
    2 — usage error (argparse)
    3 — server error frame received
    4 — server disconnected mid-session
    5 — handshake / pinning / protocol failure
    130 — Ctrl-C
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from cli import chat


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tabula",
        description="Tabula CLI: chat client and tooling for the encrypted enclave.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")

    # `chat` group with `connect` subcommand.
    chat_parser = sub.add_parser("chat", help="Chat client commands")
    chat_sub = chat_parser.add_subparsers(
        dest="chat_cmd", required=True, metavar="<chat-command>"
    )
    chat.register(chat_sub)

    # Stubs for sibling sub-issues. They register their own arguments when
    # those issues land; until then we surface a friendly "not implemented"
    # message so users discover the surface and we don't have to renumber
    # exit codes when the real impls land.
    keygen_parser = sub.add_parser(
        "keygen", help="Generate X25519 static keypair (stub — see #31)"
    )
    keygen_parser.set_defaults(_handler=_unimplemented("keygen", 31))

    servers_parser = sub.add_parser(
        "servers", help="Manage pinned server entries (stub — see #32)"
    )
    servers_parser.set_defaults(_handler=_unimplemented("servers", 32))

    return parser


def _unimplemented(name: str, issue: int) -> Callable[[argparse.Namespace], int]:
    def _handler(_args: argparse.Namespace) -> int:
        print(
            f"tabula {name}: not implemented yet (tracked in #{issue})",
            file=sys.stderr,
        )
        return 2

    return _handler


def main(argv: list[str] | None = None) -> int:
    """Entrypoint. Returns process exit code; never raises on argparse errors."""

    parser = _make_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.error("no handler registered for command")
        return 2  # unreachable; argparse exits
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
