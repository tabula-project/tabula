"""AuditLog — durable record of every distillation run.

Every Pipeline.run_once() invocation produces N AuditEntry rows, one per
distill() call. The audit log is queryable for:
  - "Which raw events became which L1 memory?" (provenance forward)
  - "What L1 memory has source X in its lineage?" (provenance back)
  - "What did the LLM see when it produced this memory?" (debugging hallucinations)

Backed by SQLite. Real implementation; scaffold == production for this scope.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import aiosqlite

from tabula_etl.types import AuditEntry


_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    distillation_id     TEXT PRIMARY KEY,
    timestamp           TEXT NOT NULL,
    distiller_name      TEXT NOT NULL,
    distiller_version   TEXT NOT NULL,
    input_event_ids     TEXT NOT NULL,           -- JSON array
    output_memory_ids   TEXT NOT NULL,           -- JSON array
    llm_provider        TEXT,
    llm_model           TEXT,
    llm_prompt_tokens   INTEGER,
    llm_completion_tokens INTEGER,
    llm_prompt_hash     TEXT,
    succeeded           INTEGER NOT NULL,        -- 0/1
    error               TEXT,
    extra               TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_timestamp_idx        ON audit (timestamp);
CREATE INDEX IF NOT EXISTS audit_distiller_idx        ON audit (distiller_name);
CREATE INDEX IF NOT EXISTS audit_succeeded_idx        ON audit (succeeded);

-- Optional: full-prompt store for debugging. Not enabled by default
-- because prompts can contain sensitive memory bodies.
CREATE TABLE IF NOT EXISTS audit_prompt (
    distillation_id TEXT PRIMARY KEY,
    prompt          TEXT NOT NULL,
    response        TEXT NOT NULL
);
"""


class AuditLog:
    def __init__(self, db_path: str | Path, *, store_full_prompts: bool = False) -> None:
        self.db_path = str(db_path)
        self.store_full_prompts = store_full_prompts
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

    async def write(self, entry: AuditEntry, *, full_prompt: str | None = None,
                    full_response: str | None = None) -> None:
        await self._ensure()
        await self._conn.execute(  # type: ignore[union-attr]
            """
            INSERT OR REPLACE INTO audit
              (distillation_id, timestamp, distiller_name, distiller_version,
               input_event_ids, output_memory_ids,
               llm_provider, llm_model, llm_prompt_tokens, llm_completion_tokens,
               llm_prompt_hash, succeeded, error, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.distillation_id,
                entry.timestamp.astimezone(timezone.utc).isoformat(),
                entry.distiller_name,
                entry.distiller_version,
                json.dumps(entry.input_event_ids),
                json.dumps(entry.output_memory_ids),
                entry.llm_provider,
                entry.llm_model,
                entry.llm_prompt_tokens,
                entry.llm_completion_tokens,
                entry.llm_prompt_hash,
                1 if entry.succeeded else 0,
                entry.error,
                json.dumps(entry.extra),
            ),
        )
        if self.store_full_prompts and full_prompt is not None:
            await self._conn.execute(  # type: ignore[union-attr]
                "INSERT OR REPLACE INTO audit_prompt (distillation_id, prompt, response) VALUES (?, ?, ?)",
                (entry.distillation_id, full_prompt, full_response or ""),
            )
        await self._conn.commit()  # type: ignore[union-attr]

    async def query_by_event(self, source_event_id: str) -> list[AuditEntry]:
        """Find all audit entries whose input_event_ids include source_event_id."""
        await self._ensure()
        # NB: SQLite has no native JSON-array contains; using LIKE on JSON text.
        # Production: use json_each() or FTS over input_event_ids.
        cur = await self._conn.execute(  # type: ignore[union-attr]
            "SELECT * FROM audit WHERE input_event_ids LIKE ?",
            (f'%"{source_event_id}"%',),
        )
        rows = await cur.fetchall()
        cols = [c[0] for c in cur.description]
        return [_row_to_entry(dict(zip(cols, r))) for r in rows]

    async def query_by_memory(self, memory_id: str) -> list[AuditEntry]:
        """Find all audit entries that produced a given L1 memory."""
        await self._ensure()
        cur = await self._conn.execute(  # type: ignore[union-attr]
            "SELECT * FROM audit WHERE output_memory_ids LIKE ?",
            (f'%"{memory_id}"%',),
        )
        rows = await cur.fetchall()
        cols = [c[0] for c in cur.description]
        return [_row_to_entry(dict(zip(cols, r))) for r in rows]

    async def stats(self) -> dict:
        await self._ensure()
        cur = await self._conn.execute(  # type: ignore[union-attr]
            "SELECT count(*) FILTER (WHERE succeeded = 1), count(*) FILTER (WHERE succeeded = 0) FROM audit"
        )
        ok, fail = await cur.fetchone()  # type: ignore[misc]
        return {"audit_total_ok": ok, "audit_total_failed": fail, "db_path": self.db_path}

    async def _ensure(self) -> None:
        if self._conn is None:
            await self.open()


def _row_to_entry(row: dict) -> AuditEntry:
    return AuditEntry(
        distillation_id=row["distillation_id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        distiller_name=row["distiller_name"],
        distiller_version=row["distiller_version"],
        input_event_ids=json.loads(row["input_event_ids"]),
        output_memory_ids=json.loads(row["output_memory_ids"]),
        llm_provider=row["llm_provider"],
        llm_model=row["llm_model"],
        llm_prompt_tokens=row["llm_prompt_tokens"],
        llm_completion_tokens=row["llm_completion_tokens"],
        llm_prompt_hash=row["llm_prompt_hash"],
        succeeded=bool(row["succeeded"]),
        error=row["error"],
        extra=json.loads(row["extra"] or "{}"),
    )
