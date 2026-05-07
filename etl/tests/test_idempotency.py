"""Unit tests for the IdempotencyStore (real, working implementation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabula_etl.idempotency import IdempotencyStore


@pytest.fixture()
async def store(tmp_path: Path) -> IdempotencyStore:
    s = IdempotencyStore(tmp_path / "idem.db")
    await s.open()
    yield s
    await s.close()


async def test_empty_already_distilled(store: IdempotencyStore) -> None:
    """Fresh store reports nothing distilled."""
    assert await store.already_distilled(["ev-1", "ev-2"]) == set()


async def test_record_and_dedup(store: IdempotencyStore) -> None:
    """After recording, those events are reported as distilled."""
    await store.record(
        source_event_ids=["ev-1", "ev-2"],
        distillation_id="01HX-TEST",
        output_memory_ids=["01HX-MEM-A"],
    )
    assert await store.already_distilled(["ev-1", "ev-2", "ev-3"]) == {"ev-1", "ev-2"}
    assert await store.already_distilled(["ev-3"]) == set()


async def test_record_is_idempotent(store: IdempotencyStore) -> None:
    """Re-recording the same event id is a no-op (preserves first distillation_id)."""
    await store.record(
        source_event_ids=["ev-1"],
        distillation_id="01HX-FIRST",
        output_memory_ids=["01HX-A"],
    )
    await store.record(
        source_event_ids=["ev-1"],
        distillation_id="01HX-SECOND",  # would be a re-attempt
        output_memory_ids=["01HX-B"],
    )
    stats = await store.stats()
    assert stats["distilled_event_count"] == 1


async def test_empty_set_query(store: IdempotencyStore) -> None:
    """Querying with no ids returns empty set without DB call."""
    assert await store.already_distilled([]) == set()
