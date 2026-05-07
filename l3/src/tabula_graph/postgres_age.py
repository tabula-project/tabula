"""PostgresAGEAdapter — the canonical Tabula L3 backend.

Postgres + Apache AGE (Cypher graph) + pgvector (embeddings) + pg_trgm (FTS).
One database, one backup, one auth. Joinable across modalities in a single SQL.

Status: scaffold. Schema + connection pool + skeleton methods implemented.
Concrete embedding pipeline, Graphiti integration, and graph-traversal Cypher
are TODO — they need a running Postgres+AGE+pgvector to test against, which
is best done in a dedicated implementation pass.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from tabula_graph.adapter import GraphAdapter
from tabula_graph.types import AudienceTag, EntityRef, Memory, SearchResult, ULID

log = logging.getLogger(__name__)


SCHEMA_VERSION = 1

# Schema bootstrap. Every statement is idempotent.
_BOOTSTRAP_SQL = """
CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- Idempotent graph creation
SELECT create_graph('tabula') WHERE NOT EXISTS (
    SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'tabula'
);

-- Memory storage (the canonical row table; the graph is derived).
CREATE TABLE IF NOT EXISTS tabula_memory (
    id              TEXT PRIMARY KEY,             -- ULID
    author          TEXT NOT NULL,
    audience        TEXT[] NOT NULL DEFAULT '{}',
    classification  TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    body_encrypted  BOOLEAN NOT NULL DEFAULT FALSE,
    encryption_keyset TEXT,
    type            TEXT,
    tags            TEXT[] NOT NULL DEFAULT '{}',
    release_trigger TEXT NOT NULL DEFAULT 'immediate',
    release_condition TEXT,
    redactions      TEXT[] NOT NULL DEFAULT '{}',
    external_ids    JSONB NOT NULL DEFAULT '{}'::jsonb,
    entities        TEXT[] NOT NULL DEFAULT '{}',
    relations       JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL,
    modified_at     TIMESTAMPTZ,
    tombstoned_at   TIMESTAMPTZ,
    embedding       vector(768),                  -- nomic-embed-text dim; configurable later
    fts             tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED
);

CREATE INDEX IF NOT EXISTS tabula_memory_author_idx       ON tabula_memory (author);
CREATE INDEX IF NOT EXISTS tabula_memory_audience_idx      ON tabula_memory USING GIN (audience);
CREATE INDEX IF NOT EXISTS tabula_memory_entities_idx      ON tabula_memory USING GIN (entities);
CREATE INDEX IF NOT EXISTS tabula_memory_tags_idx          ON tabula_memory USING GIN (tags);
CREATE INDEX IF NOT EXISTS tabula_memory_fts_idx           ON tabula_memory USING GIN (fts);
CREATE INDEX IF NOT EXISTS tabula_memory_body_trgm_idx     ON tabula_memory USING GIN (body gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tabula_memory_embedding_ivf_idx ON tabula_memory USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS tabula_memory_tombstone_idx     ON tabula_memory (tombstoned_at) WHERE tombstoned_at IS NULL;

-- Schema version pin for migrations.
CREATE TABLE IF NOT EXISTS tabula_meta (key TEXT PRIMARY KEY, value JSONB NOT NULL);
INSERT INTO tabula_meta (key, value) VALUES ('schema_version', to_jsonb(:schema_version::int))
    ON CONFLICT (key) DO NOTHING;
"""


class PostgresAGEAdapter(GraphAdapter):
    """Postgres + AGE + pgvector + pg_trgm. The reference Tabula L3 backend."""

    def __init__(
        self,
        dsn: str,
        *,
        embedding_dim: int = 768,
        embedding_fn: Any = None,  # callable: str -> list[float]; TODO: define EmbeddingFn protocol
        pool_min: int = 2,
        pool_max: int = 10,
    ) -> None:
        self.dsn = dsn
        self.embedding_dim = embedding_dim
        self.embedding_fn = embedding_fn
        self._pool: asyncpg.Pool | None = None
        self._pool_min = pool_min
        self._pool_max = pool_max

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def init(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn, min_size=self._pool_min, max_size=self._pool_max
            )
        async with self._pool.acquire() as conn:
            # NB: asyncpg doesn't support :name params; this is illustrative.
            # Real impl will swap to $1 placeholders.
            await conn.execute(
                _BOOTSTRAP_SQL.replace(":schema_version", str(SCHEMA_VERSION))
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def health(self) -> dict:
        if self._pool is None:
            return {"ok": False, "reason": "not initialized"}
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM tabula_memory WHERE tombstoned_at IS NULL"
            )
            schema_v = await conn.fetchval(
                "SELECT value FROM tabula_meta WHERE key = 'schema_version'"
            )
        return {
            "ok": True,
            "schema_version": schema_v,
            "memory_count": count,
            "embedding_dim": self.embedding_dim,
            "embedding_fn_configured": self.embedding_fn is not None,
        }

    # ── Write path ─────────────────────────────────────────────────────

    async def upsert_memory(self, memory: Memory) -> None:
        assert self._pool is not None, "call .init() first"
        embedding = (
            self.embedding_fn(memory.body)
            if self.embedding_fn and not memory.body_encrypted and memory.body
            else None
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tabula_memory
                  (id, author, audience, classification, source, body, body_encrypted,
                   encryption_keyset, type, tags, release_trigger, release_condition,
                   redactions, external_ids, entities, relations,
                   created_at, modified_at, embedding)
                VALUES
                  ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                   $13, $14::jsonb, $15, $16::jsonb, $17, $18, $19)
                ON CONFLICT (id) DO UPDATE SET
                  author          = EXCLUDED.author,
                  audience        = EXCLUDED.audience,
                  classification  = EXCLUDED.classification,
                  source          = EXCLUDED.source,
                  body            = EXCLUDED.body,
                  body_encrypted  = EXCLUDED.body_encrypted,
                  encryption_keyset = EXCLUDED.encryption_keyset,
                  type            = EXCLUDED.type,
                  tags            = EXCLUDED.tags,
                  release_trigger = EXCLUDED.release_trigger,
                  release_condition = EXCLUDED.release_condition,
                  redactions      = EXCLUDED.redactions,
                  external_ids    = EXCLUDED.external_ids,
                  entities        = EXCLUDED.entities,
                  relations       = EXCLUDED.relations,
                  modified_at     = EXCLUDED.modified_at,
                  embedding       = EXCLUDED.embedding
                WHERE tabula_memory.modified_at IS DISTINCT FROM EXCLUDED.modified_at
                   OR tabula_memory.embedding IS NULL
                """,
                memory.id,
                memory.author,
                memory.audience,
                memory.classification,
                memory.source,
                memory.body,
                memory.body_encrypted,
                memory.encryption_keyset,
                memory.type,
                memory.tags,
                memory.release_trigger,
                memory.release_condition,
                memory.redactions,
                json.dumps(memory.external_ids),
                memory.entities,
                json.dumps([r.model_dump() for r in memory.relations]),
                memory.created_at,
                memory.modified_at,
                embedding,
            )
        # TODO: also upsert AGE graph nodes/edges for entities + relations.

    async def upsert_memories(self, memories: list[Memory]) -> int:
        # TODO: optimize with executemany / COPY
        count = 0
        for m in memories:
            await self.upsert_memory(m)
            count += 1
        return count

    async def delete_memory(self, memory_id: ULID, *, hard: bool = False) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            if hard:
                await conn.execute("DELETE FROM tabula_memory WHERE id = $1", memory_id)
                # TODO: cascade-delete AGE edges
            else:
                await conn.execute(
                    "UPDATE tabula_memory SET tombstoned_at = now() WHERE id = $1",
                    memory_id,
                )

    # ── Read path ──────────────────────────────────────────────────────

    async def get_memory(self, memory_id: ULID) -> Memory | None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, author, audience, classification, source, body,
                       body_encrypted, encryption_keyset, type, tags,
                       release_trigger, release_condition, redactions,
                       external_ids, entities, relations,
                       created_at, modified_at
                FROM tabula_memory
                WHERE id = $1 AND tombstoned_at IS NULL
                """,
                memory_id,
            )
        if not row:
            return None
        return _row_to_memory(row)

    async def hybrid_search(
        self,
        query: str,
        *,
        audience: list[AudienceTag] | None = None,
        limit: int = 20,
        types: list[str] | None = None,
        since: str | None = None,
        graph_seed: list[EntityRef] | None = None,
    ) -> list[SearchResult]:
        # TODO: this is the meat of L3. Implementation needs:
        #   1. Embed `query` with self.embedding_fn
        #   2. SELECT with ORDER BY cosine_distance(embedding, $query_emb)
        #      AND ts_rank(fts, plainto_tsquery($query)) AND audience filter
        #   3. If graph_seed: AGE Cypher MATCH for proximity bonus, joined into rank
        #   4. Combine scores (weighted geometric mean)
        #
        # Skeleton returns empty list. Tests will guide the impl.
        log.warning("PostgresAGEAdapter.hybrid_search() is a scaffold; returns []")
        _ = (query, audience, limit, types, since, graph_seed)
        return []

    async def graph_traverse(
        self,
        start: EntityRef,
        *,
        relation_kinds: list[str] | None = None,
        max_depth: int = 3,
        audience: list[AudienceTag] | None = None,
    ) -> AsyncIterator[Memory]:
        # TODO: AGE Cypher MATCH (start)-[*1..max_depth]-(neighbor) WHERE ...
        # Yield Memory rows joined from tabula_memory by neighbor.id
        log.warning("PostgresAGEAdapter.graph_traverse() is a scaffold")
        if False:  # type: ignore[unreachable]
            yield  # makes this an async generator

    # ── Maintenance ────────────────────────────────────────────────────

    async def reembed(self, *, batch_size: int = 100) -> int:
        # TODO: walk all memories where embedding IS NULL OR re-embedding requested
        log.warning("PostgresAGEAdapter.reembed() is a scaffold")
        return 0

    async def rebuild_from_corpus(self, corpus_path: str) -> int:
        # TODO: pathlib walk of corpus_path/**/*.md, parse frontmatter,
        # construct Memory objects, upsert_memories in batches.
        # Mark missing-from-corpus memories as tombstoned.
        log.warning("PostgresAGEAdapter.rebuild_from_corpus() is a scaffold")
        return 0


def _row_to_memory(row: Any) -> Memory:
    """Convert an asyncpg Record to a Memory model."""
    from datetime import datetime

    from tabula_graph.types import Relation

    relations_json = row["relations"]
    if isinstance(relations_json, str):
        relations_json = json.loads(relations_json)
    external_ids_json = row["external_ids"]
    if isinstance(external_ids_json, str):
        external_ids_json = json.loads(external_ids_json)

    return Memory(
        id=row["id"],
        author=row["author"],
        audience=list(row["audience"]),
        classification=row["classification"],
        source=row["source"],
        body=row["body"],
        body_encrypted=row["body_encrypted"],
        encryption_keyset=row["encryption_keyset"],
        type=row["type"],
        tags=list(row["tags"]),
        release_trigger=row["release_trigger"],
        release_condition=row["release_condition"],
        redactions=list(row["redactions"]),
        external_ids=external_ids_json or {},
        entities=list(row["entities"]),
        relations=[Relation(**r) for r in (relations_json or [])],
        created_at=row["created_at"],
        modified_at=row["modified_at"],
    )
