# Tabula schema v1

Canonical content-type vocabulary for Tabula L1 substrate. Every record stored in a Tabula corpus has YAML frontmatter conforming to one of these types, plus a (possibly age-encrypted) markdown body.

## Layout

```
v1/
├── README.md             — this file
├── frontmatter-base.yaml — fields common to all types
└── types/
    ├── vision.yaml
    ├── person.yaml
    ├── place.yaml
    ├── event.yaml
    ├── project.yaml
    ├── tool.yaml
    ├── decision.yaml
    ├── observation.yaml
    └── conversation.yaml
```

Each `types/<name>.yaml` is a JSON Schema describing required and optional frontmatter fields for that type, written in YAML for readability. The base spec (`frontmatter-base.yaml`) defines fields every type inherits.

## v1 types

| Type | Origin | Used by | Description |
|---|---|---|---|
| `vision` | Bower | Bower, TWIN | The living "what we want our life to look like" doc. One per project or family. |
| `person` | Bower / TWIN | All three | Entity entry for a human in the graph. Account-holding or accountless. |
| `place` | Bower | Bower, TWIN | Meaningful geography (current home, future homes, family bases). |
| `event` | Bower | All three | Time-and-space coordinate (anniversary, milestone, production day). |
| `project` | Bower / TWIN | All three | Long-arc undertaking with goals and a lifecycle. |
| `tool` | Bower | Bower, TWIN | Script the agent invokes. |
| `decision` | Luce / TWIN | All three | Recorded decision with options-considered, rationale, downstream effects, outcome. |
| `observation` | TWIN | All three | Default for general notes, distilled patterns, and ETL imports. |
| `conversation` | TWIN | All three | Captured exchange (chat, email thread, meeting). |

## Extension namespaces

Consumer-specific types live in extension namespaces, not core. They follow the same frontmatter base but aren't required of every adopter.

```
schema/luce/v1/types/    — shot, sequence, task, doublet, conservation_law, ...
schema/bower/v1/types/   — TBD (chat_thread, family_decision, ...)
schema/twin/v1/types/    — TBD (handoff, persona_overlay, ...)
```

A type belongs in core when at least two consumers want it. See [../../CONTRIBUTING.md](../../CONTRIBUTING.md).

## Frontmatter format

All Tabula records use YAML frontmatter delimited by `---` lines, followed by markdown body:

```markdown
---
id: 01HXAA9ABCDEF
type: decision
created_at: 2026-05-04T18:00Z
audience: [project]
# ... type-specific fields ...
---

Markdown body.
```

The frontmatter must be plaintext (queryable). The body may be age-encrypted per audience tier; in that case the body is a single `age` ciphertext block.

## Validation

Every Tabula client should validate frontmatter against `frontmatter-base.yaml` ∪ `types/<type>.yaml` before writing. Reference implementation in `tools/validate.py` (TBD).

## Versioning

This is `v1`. Breaking changes start a new directory (`v2/`) with a deprecation cycle in `v1/`. v2 begins when at least two consumers ask for breaking changes against v1.
