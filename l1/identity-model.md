# L1 Identity & Data Model

> Source: omniscia/twin TWIN-V1.md §3.3 (substrate-relevant subset).

## Source of truth

**Markdown corpus in git.** This applies to all substrate consumers, not just any one application. Luce's `shot/sequence/task/person/decision` records, TWIN's `observation/conversation` records, and Bower's `vision/people/places/events` records all live as markdown with frontmatter, not in a database.

The graph database (L3) is a derived index. The git corpus (L1) is canonical.

## ID conventions

### Memory IDs (per-entity)

**ULIDs** (26-char Crockford base32, time-sortable).

```
01HYABCD5K2P7Q9X3Z8R4N6F2T
```

Every entity (markdown file) gets one. Used as the primary key everywhere. Time-sortable means natural chronological ordering without joining a timestamp table.

### Entity IDs (canonical references)

Namespaced slugs:

```
person:rjwalters
org:maj-foundation
production:eve
shot:eve:s001-c0001
project:tabula
tool:pi-router
```

Format: `<type>:<slug>` or `<type>:<parent>:<slug>` for nested. Used in frontmatter `entities:` lists and in cross-references between memories.

### External system IDs

Stored as **fields on canonical entities**, not as primary IDs.

```yaml
external_ids:
  kitsu_uuid: 550e8400-e29b-41d4-a716-446655440000
  github_sha: a3b4c5d6e7f8...
  notion_page_id: abc123def456
```

This means a Kitsu shot has a Tabula ULID as its primary identity, with the Kitsu UUID as a lookup field. If Kitsu disappears, the entity persists.

## Index database stack

Layer 3 query substrate. See [`../l3/README.md`](../l3/README.md) for the full spec.

- **PostgreSQL 17+** as the index host
- **Apache AGE** — Cypher-style graph queries on Postgres (replaces Neo4j as a separate database)
- **pgvector** — vector embeddings for semantic search
- **pg_trgm + native FTS** — keyword and full-text search
- **TimescaleDB** (optional) — time-series partitioning for chronological queries at scale

## Knowledge framework

**Graphiti** (MIT-licensed) orchestrates hybrid search, entity extraction and reconciliation, and provides the MCP tool-call interface for LLM agents. Tabula ships a Graphiti adapter; consumers use it directly.

## Local-first capture (optional)

**ElectricSQL** or **PowerSync** for offline writes that sync when online. Not required — git pull/push handles multi-machine sync at the corpus layer. These are for sub-second offline UX on top of the substrate.

## Browser-side analytics (optional)

**DuckDB-WASM** for queries on a corpus clone without needing a server. Useful for read-only access to a public-tier subset (heir access, public catalog browsing).
