# Multi-Archive Deployment Pattern

> Source: omniscia/twin TWIN-V1.md §3.2.

A Tabula consumer can run **multiple parallel archives**, each with its own audience tier system, encryption keys, and content scope. This is the canonical pattern for multi-org / multi-context users.

## Example: TWIN's four-archive layout

| Deployment | Owner identity | Visibility | Primary content |
|---|---|---|---|
| `omniscia/twin-substrate` | omniscia (personal OSS) | Public | The substrate engine — code, deployment templates, documentation |
| `omniscia/memory` | omniscia (personal) | Private | Cross-org strategy + observations, MAJ-context memories, inner-circle, public-tagged content |
| `omniscia/personal` | omniscia (personal) | Private | Family-tier content, spouse-only, self-only, posthumous-release content |
| `v-good/memory` | Good Studios | Private | Good Studios commercial work, productions, company strategy |
| `project-shamrock/memory` | Shamrock org | Private | Shamrock hardware product strategy, multi-founder context |

Each deployment is an instance of the substrate. They share architectural patterns and (optionally) interoperate via cross-deployment entity registry. **They do NOT share encryption keys** — each has its own audience tier system.

## Why multiple archives instead of one big one

- **Org boundaries are first-class.** An entity created in `v-good/memory` doesn't leak into `project-shamrock/memory` even if both reference the same person.
- **Different audience tier systems.** Family + spouse + self-only tiers don't apply in a Shamrock org context; trustees + co-founder + advisor tiers don't apply at home.
- **Different replication policies.** Personal archive may replicate to 4 jurisdictions; an org archive may legally need to stay in one.
- **Different access patterns.** Bots that operate on family content shouldn't have the keys to org content.

## What's shared across archives

- **Tabula substrate engine** — same Forgejo, same Postgres+AGE+pgvector, same Graphiti, same encryption library
- **Frontmatter spec** — same ULID/audience/relations format
- **Schema vocabulary** — same `vision`/`person`/`event`/etc. types
- **Tooling** — same CLI, same bots, same web UI

## What's NOT shared

- Encryption keys (each archive has its own age keyset)
- Audience tier definitions
- Replication targets
- Per-archive READMEs and onboarding docs

## Cross-archive references

Optional: a cross-deployment entity registry can let `omniscia/memory` reference an entity that lives in `v-good/memory`. The reference resolves only if the reader has read access to the target archive. This is a downstream feature; v0 deployments don't need it.

## Open-source substrate vs. private archives

The substrate (`omniscia/twin-substrate` in the example) is the shared open-source engine. **It is open-source. Anyone can deploy it.**

The archives that run on top are private to their owner. Deploying Tabula does not require sharing your archive.

## Per-consumer adaptation

Bower's deployment shape will differ — likely 1 archive per family unit, with much simpler tier structure (`family`, `parents-only`, `kids-tier-3+`). Luce's will differ again — likely 1 archive per production studio with `crew`, `clients`, `public-marketing` tiers. The pattern is invariant; the tier definitions are per-consumer.
