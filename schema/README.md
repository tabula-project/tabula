# Tabula Schema

Canonical content-type vocabulary, versioned at `v1/`.

## v1 (in progress)

| Type | Origin | Status |
|---|---|---|
| `vision` | Bower | Draft |
| `person` | Bower / TWIN | Draft |
| `place` | Bower | Draft |
| `event` | Bower | Draft |
| `project` | Bower / TWIN | Draft |
| `tool` | Bower | Draft |
| `decision` | Luce / TWIN | Draft |
| `observation` | TWIN | Draft |
| `conversation` | TWIN | Draft |

## Extension namespaces

Consumer-specific types live outside core. They follow the same frontmatter spec but aren't required of every adopter.

- `schema/luce/` — `shot`, `sequence`, `task`
- `schema/bower/` — TBD
- `schema/twin/` — TBD

## Adding a type

See [../CONTRIBUTING.md](../CONTRIBUTING.md). At least two consumers must want a type for it to land in core; otherwise it lives in an extension namespace.

## Frontmatter spec

Every Tabula record carries a YAML frontmatter block with these fields. Body follows, possibly age-encrypted.

```yaml
id: 01HYABCD5K2P7Q9X3Z8R4N6F2T          # ULID
created_at: 2026-05-04T18:00Z
modified_at: 2026-05-04T18:00Z
author: rjwalters                        # canonical entity ID
audience: [project]                      # one or more tier IDs
encryption: age:v1:keyset-2026          # null for public tier
classification: project-internal         # human-readable label
source: telegram://msg-12345             # provenance
entities:                                # canonical entity IDs referenced
  - person:omniscia
  - org:tabula-project
relations:
  references: [01HXAAA, 01HYBBB]
  supersedes: []
  caused-by: []
  is-about: [decision]
tags: [substrate, governance]
release_trigger: immediate              # immediate | conditional | delayed | posthumous
release_condition: null
redactions: []
external_ids: {}
```

Lifted from TWIN-V1 Appendix A.
