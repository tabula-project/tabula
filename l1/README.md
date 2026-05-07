# L1 — Substrate

> **Tabula Layer 1: markdown + git, frontmatter spec, schema vocabulary, encryption.**

The authoritative storage layer. Entities, decisions, observations, and all typed content live here as markdown files with YAML frontmatter, tracked in git. The knowledge graph (L3) is derived, not authoritative — rebuildable from L1 + L2 on any future infrastructure.

## Directory layout

```
l1/
├── README.md                  ← this file
├── substrate-invariant.md     ← plain text + git as the foundation
├── frontmatter-spec.md        ← canonical frontmatter schema (YAML block per file)
├── identity-model.md          ← ULIDs, entity slugs, external IDs
├── encryption.md              ← age encryption + audience tier pattern
├── connection-points.md       ← git/web/API/IPFS/email/print access surfaces
├── replication.md             ← multi-jurisdiction git replicas + industrial archival
├── capture-recall.md          ← capture/recall API + multi-machine bootstrap
└── reference/
    └── strata-patterns.md     ← L1-adapter patterns from Luce strata (reference)
```

## Content types (schema vocabulary)

Versioned in `schema/v1/`. Each type is a frontmatter schema + JSON Schema.

| Type | Origin | Used by |
|---|---|---|
| `vision` | Bower | Bower, TWIN |
| `person` | Bower / TWIN | All three |
| `place` | Bower | Bower, TWIN |
| `event` | Bower | All three |
| `project` | Bower / TWIN | All three |
| `tool` | Bower | Bower, TWIN |
| `decision` | Luce / TWIN | All three |
| `observation` | TWIN | All three |
| `conversation` | TWIN | All three |
| `shot`, `sequence`, `task` | Luce | Luce only (extension namespace) |

Luce-specific and Bower-specific types live in extension namespaces, not core. Core stays small. Adding a type = PR to Tabula.

## Substrate invariant

Plain text + git = readable by any current or future tool, rebuildable on any future infrastructure. The graph is derived; the text is canonical.
