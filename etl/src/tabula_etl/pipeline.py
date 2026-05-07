"""Pipeline — orchestrates the adapter → distiller → idempotency → audit → L1 commit flow.

This is the main entry point consumers use. They construct a Pipeline with
their adapter + distiller + the framework primitives, then call run_once()
on a schedule (or run_continuous() for a long-running daemon).

Status: scaffold-but-functional. The L1-commit step is currently "write
markdown file to a directory"; integration with consumer-defined git-commit
workflows is a TODO each consumer fills in via the `committer` callable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter  # type: ignore[import-untyped]
import ulid

from tabula_etl.adapter import Adapter
from tabula_etl.audit import AuditLog
from tabula_etl.distiller import Distiller
from tabula_etl.idempotency import IdempotencyStore
from tabula_etl.types import AuditEntry, DistilledMemory, RawEvent

log = logging.getLogger(__name__)


# A Committer takes (archive, memory_id, markdown_path) and is responsible for
# git-add + git-commit + git-push. Default writes only; consumer wires git in.
Committer = Callable[[str, str, Path], Awaitable[None]]


async def _no_op_committer(archive: str, memory_id: str, path: Path) -> None:
    log.info("committer: would commit %s/%s @ %s (no-op default)", archive, memory_id, path)


class Pipeline:
    def __init__(
        self,
        *,
        adapter: Adapter,
        distiller: Distiller,
        idempotency: IdempotencyStore,
        audit: AuditLog,
        archive_root: str | Path,
        committer: Committer = _no_op_committer,
        batch_size: int = 50,
    ) -> None:
        self.adapter = adapter
        self.distiller = distiller
        self.idempotency = idempotency
        self.audit = audit
        self.archive_root = Path(archive_root)
        self.committer = committer
        self.batch_size = batch_size

    # ── Run modes ──────────────────────────────────────────────────────

    async def run_once(self, *, since: datetime | None = None) -> dict[str, Any]:
        """One pass over the adapter's available events. Returns a summary dict.

        Suitable for cron / launchd / systemd timer triggering.
        """
        await self.idempotency.open()
        await self.audit.open()

        n_seen = 0
        n_skipped_idem = 0
        n_distilled = 0
        n_committed = 0
        n_failed = 0

        batch: list[RawEvent] = []
        async for ev in self.adapter.fetch(since=since):
            n_seen += 1
            batch.append(ev)
            if len(batch) >= self.batch_size:
                outcome = await self._process_batch(batch)
                n_skipped_idem += outcome["skipped_idem"]
                n_distilled += outcome["distilled"]
                n_committed += outcome["committed"]
                n_failed += outcome["failed"]
                batch = []
        if batch:
            outcome = await self._process_batch(batch)
            n_skipped_idem += outcome["skipped_idem"]
            n_distilled += outcome["distilled"]
            n_committed += outcome["committed"]
            n_failed += outcome["failed"]

        return {
            "seen": n_seen,
            "skipped_idem": n_skipped_idem,
            "distilled": n_distilled,
            "committed": n_committed,
            "failed": n_failed,
        }

    # ── Internal ───────────────────────────────────────────────────────

    async def _process_batch(self, batch: list[RawEvent]) -> dict[str, int]:
        # 1. Idempotency: filter out already-distilled events.
        already = await self.idempotency.already_distilled(ev.source_id for ev in batch)
        fresh = [ev for ev in batch if ev.source_id not in already]
        skipped_idem = len(batch) - len(fresh)

        if not fresh:
            return {"skipped_idem": skipped_idem, "distilled": 0, "committed": 0, "failed": 0}

        # 2. Distill.
        distillation_id = str(ulid.new())
        try:
            memories = await self.distiller.distill(fresh)
        except Exception as exc:  # noqa: BLE001
            log.exception("distiller.distill() raised; recording failure")
            await self.audit.write(
                AuditEntry(
                    distillation_id=distillation_id,
                    timestamp=datetime.now(tz=timezone.utc),
                    distiller_name=self.distiller.name,
                    distiller_version=self.distiller.version,
                    input_event_ids=[ev.source_id for ev in fresh],
                    output_memory_ids=[],
                    succeeded=False,
                    error=str(exc),
                )
            )
            return {"skipped_idem": skipped_idem, "distilled": 0, "committed": 0, "failed": len(fresh)}

        # 3. Commit each memory to L1 (markdown + frontmatter).
        committed_ids: list[str] = []
        for mem in memories:
            try:
                memory_id = mem.frontmatter.get("id") or str(ulid.new())
                mem.frontmatter["id"] = memory_id
                path = await self._write_l1_file(mem, memory_id)
                await self.committer(mem.archive, memory_id, path)
                committed_ids.append(memory_id)
            except Exception as exc:  # noqa: BLE001
                log.exception("commit failed for memory %s", mem.frontmatter.get("id"))
                # Record partial success in audit; don't poison idempotency.
                await self.audit.write(
                    AuditEntry(
                        distillation_id=distillation_id,
                        timestamp=datetime.now(tz=timezone.utc),
                        distiller_name=self.distiller.name,
                        distiller_version=self.distiller.version,
                        input_event_ids=[s for s in mem.source_event_ids],
                        output_memory_ids=[],
                        succeeded=False,
                        error=f"commit failed: {exc}",
                    )
                )

        # 4. Audit + idempotency.
        await self.audit.write(
            AuditEntry(
                distillation_id=distillation_id,
                timestamp=datetime.now(tz=timezone.utc),
                distiller_name=self.distiller.name,
                distiller_version=self.distiller.version,
                input_event_ids=[ev.source_id for ev in fresh],
                output_memory_ids=committed_ids,
                succeeded=len(committed_ids) > 0 or len(memories) == 0,
            )
        )
        await self.idempotency.record(
            source_event_ids=[ev.source_id for ev in fresh],
            distillation_id=distillation_id,
            output_memory_ids=committed_ids,
        )

        return {
            "skipped_idem": skipped_idem,
            "distilled": len(memories),
            "committed": len(committed_ids),
            "failed": len(memories) - len(committed_ids),
        }

    async def _write_l1_file(self, mem: DistilledMemory, memory_id: str) -> Path:
        archive_dir = self.archive_root / mem.archive / mem.type
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = archive_dir / f"{memory_id}.md"
        post = frontmatter.Post(content=mem.body, **mem.frontmatter)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")
        return path
