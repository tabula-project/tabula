# Strata Patterns — L1 Reference Designs from Luce

> Source: goodstudios-dev/luce/strata. Code stays in Luce (it's L4 — Luce's creative provenance application). The *patterns* are reference designs for any Tabula consumer building L1 adapters.

Luce's strata project implemented four subsystems (Roll/Keep/Register/Witness) before Tabula existed as a separate substrate. The code is Luce-flavored (Om coupling weights, GIM doublets, conservation laws), but the architectural patterns are substrate-level and worth documenting here so other Tabula consumers can borrow them.

## Pattern 1: Typed-entity stores with FastMCP

Each subsystem in strata is:
- A **store** (async SQLite via `aiosqlite`) for fast operational reads
- A **server** exposing 3–4 tools via FastMCP
- A **models.py** with Pydantic v2 schemas
- Backed by a corresponding L1 markdown directory

This shape is the canonical L1-adapter pattern: SQLite for hot operational reads, markdown+git for canonical durability, FastMCP for agent integration.

## Pattern 2: Roll — identity & relationships

Roll provides user/relationship CRUD with explicit relationship strength weights:

| Relationship | Weight | Frontmatter equivalent |
|---|---|---|
| Rights holder | 1.0 | `audience: org-<owner>` |
| Employee | 1.0 | `audience: org-<consumer>` |
| Collaborator | 0.9 | `audience: inner-circle` |
| Contributor | 0.7 | `audience: contributor` |
| Participant | 0.3 | `audience: public` |

Tabula's frontmatter `audience:` field is the substrate-level expression of the same pattern. Luce uses Om coupling weights (a continuous 0.0–1.0 score); Tabula uses discrete audience tiers. Different consumers can pick the granularity that fits.

## Pattern 3: Keep — content with provenance

Keep stores catalog/work/asset entries with:
- **SHA-256 checksums** for content addressing
- **Filesystem organization by work_id** (paths like `<work-id>/<asset-id>.<ext>`)
- **Availability tiers** (`listen / read / view / reference / sample / license / collaborate`)

Tabula's L1 markdown corpus uses the same content-addressing principle (ULIDs in frontmatter, files organized by archive + audience). The "availability tier" enum is Luce-specific (creative-work licensing); Tabula's `audience:` + `release_trigger:` cover the equivalent for general substrate use.

## Pattern 4: Register — the "Higgs event"

Register tracks the moment content **couples to the knowledge graph**: catalog ingestion jobs, metadata extraction from files, progress verification. Conceptually: when a new L1 entity appears, Register watches it become indexed in L3.

Tabula's ETL framework (see [`../../etl/README.md`](../../etl/README.md)) has the same job: distill L2 events into L1, then ensure L3 picks them up. Strata's Register is the worked example of how to verify the L1 → L3 coupling completed correctly.

## Pattern 5: Witness — observation + lineage

Witness records every interaction (search, view, sample, reference) as an `observation` with a coupling weight, and tracks lineage (what created what, with bidirectional queries and self-reference prevention).

This maps directly to Tabula's `observation` content type and the `relations: caused-by` / `relations: references` frontmatter fields. The `coupling weight` is Luce-specific; the structural pattern (typed observation entries with lineage relations) is substrate-level.

## What to do with this

- **Don't import strata code into Tabula.** It's L4. Importing it would entangle Tabula with Luce's physics framework.
- **Do borrow the architectural shape.** When a consumer (Bower, TWIN-personal, or future) builds its L1 adapters, the four-subsystem pattern (Roll/Keep/Register/Witness equivalents) is a known-good starting point.
- **Cross-reference the strata code** for implementation details: `goodstudios-dev/luce/strata/src/strata/`. The `db.py` (102 lines, async SQLite init) and the `<subsystem>/store.py` files are particularly clean reference reads.
