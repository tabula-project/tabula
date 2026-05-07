"""tabula-graph CLI entry point.

    tabula-graph init    --dsn postgresql://...
    tabula-graph health  --dsn postgresql://...
    tabula-graph rebuild --dsn postgresql://... --corpus /path/to/L1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from tabula_graph.postgres_age import PostgresAGEAdapter


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tabula-graph", description="Tabula L3 graph CLI")
    p.add_argument("--dsn", required=True, help="PostgreSQL DSN")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Idempotent schema bootstrap")
    sub.add_parser("health", help="Show graph health snapshot")

    rebuild = sub.add_parser("rebuild", help="Rebuild from L1 corpus")
    rebuild.add_argument("--corpus", required=True, help="Path to L1 corpus checkout")

    sub.add_parser("reembed", help="Re-compute all embeddings (long-running)")
    return p


async def _run(args: argparse.Namespace) -> int:
    adapter = PostgresAGEAdapter(dsn=args.dsn)
    await adapter.init()
    try:
        if args.cmd == "init":
            print("✓ schema initialized", file=sys.stderr)
            return 0
        if args.cmd == "health":
            h = await adapter.health()
            print(json.dumps(h, indent=2, default=str))
            return 0
        if args.cmd == "rebuild":
            n = await adapter.rebuild_from_corpus(args.corpus)
            print(f"✓ rebuilt; processed {n} memories", file=sys.stderr)
            return 0
        if args.cmd == "reembed":
            n = await adapter.reembed()
            print(f"✓ re-embedded {n} memories", file=sys.stderr)
            return 0
    finally:
        await adapter.close()
    return 1


def main() -> int:
    parser = _make_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
