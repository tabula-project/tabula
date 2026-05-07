"""Type round-trip tests. Pure-Python; no Postgres needed."""

from __future__ import annotations

from datetime import datetime, timezone

from tabula_graph.types import Memory, Relation, SearchResult


def test_memory_minimum_fields() -> None:
    """A memory with only required fields validates."""
    m = Memory(
        id="01HYABCD5K2P7Q9X3Z8R4N6F2T",
        author="person:omniscia",
        audience=["public"],
        created_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        source="cli://capture",
    )
    assert m.id.startswith("01H")
    assert m.audience == ["public"]
    assert m.body == ""
    assert m.relations == []
    assert m.release_trigger == "immediate"


def test_memory_full_roundtrip() -> None:
    m = Memory(
        id="01HYABCD5K2P7Q9X3Z8R4N6F2T",
        author="person:omniscia",
        audience=["org-maj", "inner-circle"],
        created_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        modified_at=datetime(2026, 5, 7, 12, tzinfo=timezone.utc),
        source="telegram://botho/msg-12345",
        classification="org-internal",
        body="we agreed to use R2 over B2 because of egress costs",
        body_encrypted=False,
        type="decision",
        tags=["infra", "storage"],
        entities=["person:rjwalters", "org:maj-foundation"],
        relations=[
            Relation(kind="references", target="01HXAAA000000000000000"),
            Relation(kind="caused-by", target="01HXBBB000000000000000"),
        ],
        external_ids={"github_sha": "a3b4c5d"},
    )
    j = m.model_dump_json()
    m2 = Memory.model_validate_json(j)
    assert m2 == m


def test_relation_kinds_constrained() -> None:
    """Only the four canonical relation kinds are accepted."""
    import pytest
    from pydantic import ValidationError

    Relation(kind="references", target="01HX0000")
    Relation(kind="supersedes", target="01HX0000")
    Relation(kind="caused-by", target="01HX0000")
    Relation(kind="is-about", target="01HX0000")

    with pytest.raises(ValidationError):
        Relation(kind="invalid-kind", target="01HX0000")  # type: ignore[arg-type]


def test_search_result_optional_subscores() -> None:
    """Sub-scores can be None when a search modality didn't contribute."""
    r = SearchResult(memory_id="01HX0000", score=0.85, snippet="...")
    assert r.semantic_score is None
    assert r.audience == []
