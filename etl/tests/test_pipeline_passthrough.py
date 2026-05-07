"""End-to-end test: FileAdapter → PassthroughDistiller → markdown commit.

This validates the framework wiring without needing Postgres/Graphiti.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tabula_etl.adapter import FileAdapter
from tabula_etl.audit import AuditLog
from tabula_etl.distiller import PassthroughDistiller
from tabula_etl.idempotency import IdempotencyStore
from tabula_etl.pipeline import Pipeline


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    src = tmp_path / "events"
    src.mkdir()
    (src / "001.txt").write_text("first event body")
    (src / "002.txt").write_text("second event body")
    return src


async def test_pipeline_writes_l1_files(tmp_path: Path, source_dir: Path) -> None:
    archive_root = tmp_path / "archives"
    pipeline = Pipeline(
        adapter=FileAdapter(path=str(source_dir), glob="*.txt"),
        distiller=PassthroughDistiller(archive="test/memory", audience=["self-only"]),
        idempotency=IdempotencyStore(tmp_path / "idem.db"),
        audit=AuditLog(tmp_path / "audit.db"),
        archive_root=archive_root,
    )

    result = await pipeline.run_once()

    assert result["seen"] == 2
    assert result["distilled"] == 2
    assert result["committed"] == 2
    assert result["failed"] == 0

    # Verify L1 files were written.
    type_dir = archive_root / "test" / "memory" / "observation"
    assert type_dir.is_dir()
    md_files = list(type_dir.glob("*.md"))
    assert len(md_files) == 2

    # Verify the body landed.
    bodies = "\n".join(p.read_text() for p in md_files)
    assert "first event body" in bodies
    assert "second event body" in bodies


async def test_pipeline_idempotent_second_run(tmp_path: Path, source_dir: Path) -> None:
    """Re-running the pipeline over the same source skips already-distilled events."""
    archive_root = tmp_path / "archives"
    idem = IdempotencyStore(tmp_path / "idem.db")
    audit = AuditLog(tmp_path / "audit.db")

    def mk():
        return Pipeline(
            adapter=FileAdapter(path=str(source_dir), glob="*.txt"),
            distiller=PassthroughDistiller(archive="test/memory", audience=["self-only"]),
            idempotency=idem,
            audit=audit,
            archive_root=archive_root,
        )

    r1 = await mk().run_once()
    assert r1["distilled"] == 2

    r2 = await mk().run_once()
    assert r2["seen"] == 2
    assert r2["skipped_idem"] == 2
    assert r2["distilled"] == 0
    assert r2["committed"] == 0

    await idem.close()
    await audit.close()
