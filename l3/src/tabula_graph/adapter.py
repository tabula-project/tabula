"""GraphAdapter — the abstract interface every L3 backend implements.

Tabula ships PostgresAGEAdapter as the canonical implementation. Consumers can
write alternative adapters (in-memory for tests, Neo4j for benchmarks, etc.)
that conform to this Protocol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from tabula_graph.types import AudienceTag, EntityRef, Memory, SearchResult, ULID


class GraphAdapter(ABC):
    """The contract between Tabula consumers and an L3 graph backend.

    Lifecycle:
        adapter = SomeAdapter(...)
        await adapter.init()        # idempotent; creates schema if missing
        ...                         # upsert / search / get
        await adapter.close()
    """

    # ── Lifecycle ─────────────────────────────────────────────────────

    @abstractmethod
    async def init(self) -> None:
        """Initialize backend (extensions, tables, indexes). Must be idempotent."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources. Must be idempotent."""

    @abstractmethod
    async def health(self) -> dict:
        """Return a health snapshot: connectivity, schema version, embedding model, counts."""

    # ── Write path ─────────────────────────────────────────────────────

    @abstractmethod
    async def upsert_memory(self, memory: Memory) -> None:
        """Add or update one memory in the graph.

        Idempotent on `memory.id` (ULID). Re-running with the same memory is a no-op
        if `modified_at` hasn't advanced; otherwise updates the node + re-embeds + re-edges.
        """

    @abstractmethod
    async def upsert_memories(self, memories: list[Memory]) -> int:
        """Bulk upsert. Returns count actually written (idempotent skips don't count).

        Implementations should batch internally for efficiency.
        """

    @abstractmethod
    async def delete_memory(self, memory_id: ULID, *, hard: bool = False) -> None:
        """Remove a memory from the graph.

        `hard=False` (default) marks tombstoned but retains lineage for audit.
        `hard=True` removes the node + its edges entirely (use only for compliance/GDPR).
        """

    # ── Read path ──────────────────────────────────────────────────────

    @abstractmethod
    async def get_memory(self, memory_id: ULID) -> Memory | None:
        """Fetch a single memory by ULID. Returns None if missing or tombstoned."""

    @abstractmethod
    async def hybrid_search(
        self,
        query: str,
        *,
        audience: list[AudienceTag] | None = None,
        limit: int = 20,
        types: list[str] | None = None,
        since: str | None = None,  # ISO 8601
        graph_seed: list[EntityRef] | None = None,
    ) -> list[SearchResult]:
        """The main retrieval entry point.

        Combines:
          - pgvector cosine similarity over embedded bodies
          - pg_trgm + native FTS keyword ranking
          - Apache AGE graph traversal proximity bonus from `graph_seed`

        Filters by `audience` (the requesting tier set). Memories whose audience
        list does NOT intersect with the requesting set are excluded — this is
        the security boundary, not a UI nicety.
        """

    @abstractmethod
    async def graph_traverse(
        self,
        start: EntityRef,
        *,
        relation_kinds: list[str] | None = None,
        max_depth: int = 3,
        audience: list[AudienceTag] | None = None,
    ) -> AsyncIterator[Memory]:
        """Walk the graph from an entity reference. Yields memories in BFS order.

        Used for "show me everything connected to person:rjwalters within 3 hops."
        """

    # ── Maintenance ────────────────────────────────────────────────────

    @abstractmethod
    async def reembed(self, *, batch_size: int = 100) -> int:
        """Re-compute embeddings for all memories.

        Used after an embedding-model upgrade. Returns count re-embedded.
        Long-running; callers should expect this to take O(minutes-hours).
        """

    @abstractmethod
    async def rebuild_from_corpus(self, corpus_path: str) -> int:
        """Walk a git-checked-out L1 corpus and populate the graph from scratch.

        Idempotent: existing memories are upserted, missing ones added,
        graph-only memories tombstoned. Returns count processed.

        This is the disaster-recovery primitive. Given just an L1 corpus clone,
        a fresh L3 can be reconstructed in O(corpus-size) time.
        """
