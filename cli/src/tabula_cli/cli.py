"""``tabula`` CLI entry point.

Currently exposes the ``enclave`` subcommand group. Other top-level groups
(audit, schema, etc.) will plug into the same parser as they land.
"""

from __future__ import annotations

import argparse
import sys

from tabula_cli import enclave as enclave_mod


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tabula",
        description="Tabula CLI: enclave lifecycle and substrate utilities.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    enclave_mod.add_subparser(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the desired process exit code."""
    parser = _make_parser()
    args = parser.parse_args(argv)
    if args.cmd == "enclave":
        return enclave_mod.run(args)
    parser.error(f"unknown command: {args.cmd}")
    return 1  # unreachable; parser.error exits


if __name__ == "__main__":
    sys.exit(main())
