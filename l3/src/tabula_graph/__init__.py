"""tabula-graph — L3 knowledge graph for Tabula substrate consumers.

The graph is *derived*, not authoritative. The L1 markdown corpus + L2 operational
log are the sources of truth; this package builds and queries Postgres + Apache AGE
+ pgvector indexes on top.

Public API:

    from tabula_graph import GraphAdapter, PostgresAGEAdapter, EntityRef, Memory

    adapter = PostgresAGEAdapter(dsn="postgresql://localhost/tabula")
    await adapter.init()
    await adapter.upsert_memory(memory)
    results = await adapter.hybrid_search("lottery progressivity", audience=["public", "inner-circle"])
"""

from tabula_graph.types import (
    EntityRef,
    Memory,
    Relation,
    SearchResult,
    AudienceTag,
)
from tabula_graph.adapter import GraphAdapter
from tabula_graph.postgres_age import PostgresAGEAdapter

__version__ = "0.1.0"

__all__ = [
    "EntityRef",
    "Memory",
    "Relation",
    "SearchResult",
    "AudienceTag",
    "GraphAdapter",
    "PostgresAGEAdapter",
]
