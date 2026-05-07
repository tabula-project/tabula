# L3 — Knowledge graph

> **Tabula Layer 3: Graphiti over Postgres + Apache AGE + pgvector. Live query surface, derived from L1 + L2.**

## Status

Skeleton. Concrete adapter code lives in consumers' codebases for now; this directory holds the canonical specification for how the L3 layer is structured and how it relates to L1 and L2.

## What L3 is

The query layer. Sub-second graph traversal, semantic search, and entity reconciliation for agents and humans alike. **Derived, not authoritative** — rebuildable from L1 + L2 on any future infrastructure. If the index dies, the substrate doesn't lose information; only query latency degrades during rebuild.

## Stack

| Component | Role | Why this choice |
|---|---|---|
| **PostgreSQL 17+** | Foundation database | Most durable, well-understood, MIT-compatible OSS |
| **Apache AGE** | Graph queries via Cypher on Postgres | Avoids running Neo4j as a separate database; one ops surface |
| **pgvector** | Vector embeddings for semantic search | Co-located with graph + relational; one query plane |
| **Graphiti** | Knowledge graph framework on top | Entity extraction, episode-based ingestion, MCP tool interface for agents |
| **pg_trgm + native FTS** | Keyword and full-text search | Hybrid search complement to vector |
| **TimescaleDB** (optional) | Time-series partitioning at scale | For consumers with chronological-heavy queries (Luce production timelines) |

All open-source. No single-vendor lock-in.

## How L3 is fed

```
L1 (markdown commits) ──┐
                        ├─→ Graphiti ingestion → L3 query graph
L2 (operational events)─┘
```

Two ingestion paths into the same Graphiti graph:

1. **From L1 commits.** A git post-commit hook (or watcher) detects new/changed markdown files in the consumer's L1 archive. Frontmatter is parsed; structured fields (entities, relations, audience, type) are written directly to the graph. Markdown body is optionally fed through Graphiti's LLM extraction for additional implicit entity/relationship discovery.

2. **From L2 events.** The dual-tier memory pattern's distillation pipeline emits L1 records, which feed L3 via path 1. Some L2 events (e.g., low-signal MCP tool calls in Luce) feed L3 directly as ephemeral episodes that age out without ever being promoted to L1.

The `agree-on-source-of-truth` rule: if a record exists in L1, L1 is canonical for that record. L3 reflects L1 (with some lag). L2 events that haven't been distilled to L1 may still be queryable in L3 as ephemeral episodes, but those age out per the consumer's L2 rotation policy.

## Two ingestion modes

Graphiti's native `add_episode()` takes narrative text and runs LLM extraction. This works for some Tabula use cases (long-form observation distillation) but is wrong for others (structured frontmatter facts that shouldn't go through LLM hallucination risk).

| Mode | When to use | How it works |
|---|---|---|
| **Direct frontmatter ingestion** | Structured fields: `entities`, `relations`, `audience`, `type` | Bypass Graphiti's LLM extraction; write directly to AGE via Cypher. Frontmatter is the API. |
| **Episode extraction** | Markdown body content | Standard `Graphiti.add_episode(body_text)`. LLM extracts implicit entities + relationships beyond what frontmatter declares. |

A consumer's L1 → L3 sync uses both: structured fields go direct; narrative body goes through extraction. This is the [pattern that requires real engineering](../docs/patterns/dual-tier-memory.md#path-c-implementation-considerations) and is the substantive cost of substrate-first design vs graph-primary.

## Query interfaces

Agents and humans query L3 through:

| Interface | Use |
|---|---|
| **Graphiti MCP tools** | Agent-driven graph queries via FastMCP |
| **Direct Cypher (over AGE)** | Power-user / debug / index-rebuild |
| **pgvector similarity search** | Semantic recall ("find decisions about storage") |
| **Hybrid search (Graphiti)** | Combined vector + graph + full-text scoring |
| **REST/GraphQL** (consumer-defined) | Application-side query shapes; Tabula doesn't standardize |

## Performance bounds

Graphiti is designed for production scale (Zep team's lineage). Tabula consumers operate at scales well within Graphiti's design envelope:

| Consumer | Typical L3 graph size | Query frequency |
|---|---|---|
| TWIN-personal | ~1K–10K entities, ~10K–100K edges | Episodic (~100/day) |
| Bower (per family) | ~1K entities, ~10K edges | Per-message classifier (~50/day) |
| Luce (per studio) | ~10K–100K entities, ~1M edges | Sub-second per agent action (~10K/day) |

For each, the bottleneck is L1 → L3 sync latency, not query throughput. Sync should land within seconds of an L1 commit; query latency is sub-100ms typical.

## Index discipline

L3 is a **cache**, not a source of truth.

- Drop L3, rebuild from L1 + L2: must always work. The integrity audit is part of the [`reset()` lifecycle stage](../docs/patterns/lifecycle-vocabulary.md#reset-periodic-archival-rotation).
- Schema migrations in Graphiti (or AGE, or pgvector) require: rebuild path tested; consumer's index can be torn down and re-fed without loss.
- "Drift" (L3 reflects stale state vs L1) is a heart_beat() failure; surface as audit alert.

## What L3 does NOT do

- **L3 is not the place to put facts you can't reproduce from L1+L2.** If you can't rebuild it, it belongs in L1.
- **L3 is not a data warehouse.** It's a query graph for live agent/human queries. Historical analytics with heavy joins should run on L1+L2 directly (or a separate analytics replica), not on the live L3.
- **L3 does not own privacy enforcement.** Privacy class is enforced at the [L0 router](../l0/docs/router.md). L3 trusts the audience-tier metadata in records; it doesn't itself decide what should be encrypted vs plaintext.

## Cross-references

- L1 substrate: [`l1/README.md`](../l1/README.md)
- L1 frontmatter spec (the API L3 ingests from): [`l1/frontmatter-spec.md`](../l1/frontmatter-spec.md)
- L2 event format (also feeds L3): [`etl/README.md`](../etl/README.md)
- L0 router (privacy enforcement): [`l0/docs/router.md`](../l0/docs/router.md)
- Lifecycle vocabulary (when L3 rebuilds): [`docs/patterns/lifecycle-vocabulary.md`](../docs/patterns/lifecycle-vocabulary.md)
- Five-layer architecture: [`docs/five-layer-architecture.md`](../docs/five-layer-architecture.md)
- Origin: TWIN-V1.md §3.3 (database choices and identity/data model)

## Reference implementation status

| Consumer | L3 status |
|---|---|
| TWIN-personal | Not yet built; v0 substrate-first scope (L1 only) — L3 deferred to v0.2 |
| Luce | Neo4j+Graphiti running (`luce-neo4j` Docker container); 24 nodes test data; production code uses Graphiti's API. Migration to Postgres+AGE+Graphiti tracked in luce bead `lu-ib7` |
| Bower | Concept; L3 lights up when concierge classifier needs cross-record context |

The shared abstraction is the Graphiti API. Whether the underlying database is Neo4j (Luce's current) or Postgres+AGE (Tabula's reference) is a deployment-time choice; consumers can run different backends and still share L1+L4 patterns.
