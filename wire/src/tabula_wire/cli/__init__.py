"""Top-level ``tabula`` CLI entrypoint.

Subcommands register themselves in :func:`_make_parser`. Today only
``keygen`` is implemented (this PR / issue #31). Future subcommands:

- ``chat connect`` (#29)
- ``pin add/list/remove`` (#32)

The ``console_scripts`` entrypoint in ``pyproject.toml`` points at
:func:`main`.
"""

from __future__ import annotations

import argparse
import sys

from tabula_wire.cli import keygen as _keygen


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tabula",
        description="Tabula transport CLI (keygen, chat, pin).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    _keygen.register(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.error(f"no handler registered for command {args.cmd!r}")
        return 2  # unreachable; argparse exits

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
