"""tabula-etl — L2 → L1 distillation framework.

Tabula consumers use this framework to promote raw operational events (L2) into
typed L1 entities. The framework handles batching, idempotency, dedup, and audit.
The consumer provides source adapters and distillation rules.

Public API:

    from tabula_etl import Adapter, Distiller, Pipeline, IdempotencyStore, AuditLog

    class MyAdapter(Adapter):
        async def fetch(self, since): ...

    class MyDistiller(Distiller):
        async def distill(self, batch): ...

    pipeline = Pipeline(
        adapter=MyAdapter(),
        distiller=MyDistiller(),
        idempotency=IdempotencyStore("./.runtime/etl-idem.db"),
        audit=AuditLog("./.runtime/etl-audit.db"),
    )
    await pipeline.run_once()
"""

from tabula_etl.types import RawEvent, DistilledMemory, AuditEntry
from tabula_etl.adapter import Adapter
from tabula_etl.distiller import Distiller
from tabula_etl.idempotency import IdempotencyStore
from tabula_etl.audit import AuditLog
from tabula_etl.pipeline import Pipeline

__version__ = "0.1.0"

__all__ = [
    "RawEvent",
    "DistilledMemory",
    "AuditEntry",
    "Adapter",
    "Distiller",
    "IdempotencyStore",
    "AuditLog",
    "Pipeline",
]
