"""IdempotencyStore — deduplication primitive for the ETL pipeline.

Backed by SQLite. Stores: (source_event_id, distillation_id, output_memory_ids[]).
Before a batch hits the distiller, the pipeline filters out events whose
source_event_id already has a successful distillation row.

This is a real, usable implementation — small enough scope that scaffold
== production. Production hardening (concurrency safety, WAL mode, vacuuming)
is straightforward and noted in TODOs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import aiosqlite


_SCHEMA = """
CREATE TABLE IF NOT EXISTS idem (
    source_event_id   TEXT PRIMARY KEY,
    distillation_id   TEXT NOT NULL,
    output_memory_ids TEXT NOT NULL,             -- JSON array of ULIDs
    distilled_at      TEXT NOT NULL              -- ISO 8601 UTC
);
CREATE INDEX IF NOT EXISTS idem_distillation_idx ON idem (distillation_id);
CREATE INDEX IF NOT EXISTS idem_distilled_at_idx ON idem (distilled_at);
"""


class IdempotencyStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path)
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.executescript(_SCHEMA)
            await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def already_distilled(self, source_event_ids: Iterable[str]) -> set[str]:
        """Return the subset of provided ids that have a row in the idem table."""
        await self._ensure()
        ids = list(source_event_ids)
        if not ids:
            return set()
        placeholders = ",".join("?" * len(ids))
        cur = await self._conn.execute(  # type: ignore[union-attr]
            f"SELECT source_event_id FROM idem WHERE source_event_id IN ({placeholders})",
            ids,
        )
        rows = await cur.fetchall()
        return {r[0] for r in rows}

    async def record(
        self,
        *,
        source_event_ids: list[str],
        distillation_id: str,
        output_memory_ids: list[str],
    ) -> None:
        """Atomically record that this batch was distilled. Idempotent on source_event_id."""
        await self._ensure()
        now = datetime.now(tz=timezone.utc).isoformat()
        memo_json = json.dumps(output_memory_ids)
        async with self._conn.execute("BEGIN") as _:  # type: ignore[union-attr]
            for sid in source_event_ids:
                await self._conn.execute(  # type: ignore[union-attr]
                    "INSERT OR IGNORE INTO idem (source_event_id, distillation_id, output_memory_ids, distilled_at) "
                    "VALUES (?, ?, ?, ?)",
                    (sid, distillation_id, memo_json, now),
                )
        await self._conn.commit()  # type: ignore[union-attr]

    async def stats(self) -> dict:
        await self._ensure()
        cur = await self._conn.execute("SELECT count(*) FROM idem")  # type: ignore[union-attr]
        (n,) = await cur.fetchone()  # type: ignore[misc]
        return {"distilled_event_count": n, "db_path": self.db_path}

    async def _ensure(self) -> None:
        if self._conn is None:
            await self.open()
