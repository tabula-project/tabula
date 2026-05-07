"""tabula-etl CLI entry point.

    tabula-etl run-once --adapter file --path /tmp/events --archive omniscia/self
    tabula-etl stats    --idem-db .runtime/idem.db --audit-db .runtime/audit.db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from tabula_etl.adapter import FileAdapter
from tabula_etl.audit import AuditLog
from tabula_etl.distiller import PassthroughDistiller
from tabula_etl.idempotency import IdempotencyStore
from tabula_etl.pipeline import Pipeline


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tabula-etl", description="Tabula L2→L1 ETL pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run-once", help="One pass over the adapter source")
    r.add_argument("--adapter", choices=["file"], default="file")
    r.add_argument("--path", required=True, help="Source path for FileAdapter")
    r.add_argument("--archive", required=True, help="Target L1 archive name")
    r.add_argument("--archive-root", required=True, help="Local L1 corpus root")
    r.add_argument("--audience", default="self-only", help="Comma-separated audience tags")
    r.add_argument("--idem-db", default=".runtime/etl-idem.db")
    r.add_argument("--audit-db", default=".runtime/etl-audit.db")

    s = sub.add_parser("stats", help="Idempotency + audit stats")
    s.add_argument("--idem-db", default=".runtime/etl-idem.db")
    s.add_argument("--audit-db", default=".runtime/etl-audit.db")

    return p


async def _run(args: argparse.Namespace) -> int:
    if args.cmd == "run-once":
        adapter = FileAdapter(path=args.path)
        distiller = PassthroughDistiller(
            archive=args.archive,
            audience=[a.strip() for a in args.audience.split(",") if a.strip()],
        )
        idem = IdempotencyStore(args.idem_db)
        audit = AuditLog(args.audit_db)
        pipeline = Pipeline(
            adapter=adapter,
            distiller=distiller,
            idempotency=idem,
            audit=audit,
            archive_root=args.archive_root,
        )
        result = await pipeline.run_once()
        print(json.dumps(result, indent=2))
        await idem.close()
        await audit.close()
        await adapter.close()
        return 0
    if args.cmd == "stats":
        idem = IdempotencyStore(args.idem_db)
        audit = AuditLog(args.audit_db)
        await idem.open()
        await audit.open()
        out = {"idempotency": await idem.stats(), "audit": await audit.stats()}
        print(json.dumps(out, indent=2))
        await idem.close()
        await audit.close()
        return 0
    return 1


def main() -> int:
    parser = _make_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
