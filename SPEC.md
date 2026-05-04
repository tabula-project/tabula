# Tabula

**A sovereign-AI common core: privacy-class routing, cold-by-default compute, decadal substrate.**

*Status: Concept (May 2026)*
*Stewards (proposed): @omniscia (TWIN), Vivake / Good Studios (Luce), Robb / 2AM Logic (Bower)*
*License (proposed): Apache 2.0*

---

## What Tabula is

Tabula is the shared infrastructure beneath three independent applications — TWIN, Luce, and Bower — that each need the same things: sovereign-class compute, durable typed memory, and a knowledge graph derived from both. Rather than each project rebuilding its own, Tabula is the open-source substrate they jointly depend on.

The name comes from the Roman *tabula* — the wax-coated writing surface that was the literal substrate of recorded knowledge for a thousand years. *Tabula rasa* — the blank slate, ready to be written on — is the foundational metaphor for any substrate. The system is named for that: a durable, owned writing surface beneath whatever tools come and go on top of it.

## Why Tabula exists

Three teams independently arrived at the same core architecture:

- **TWIN-personal** (@omniscia) — personal continuity substrate; markdown corpus, audience-tiered access, posthumous-survivable.
- **Luce** (Good Studios) — production-tracking platform; doublets, conservation laws, decision traces.
- **Bower** (2AM Logic) — encrypted family workspace; agent-as-peer over a private git repo.

Each was reinventing: typed entities + relationships + decisions accreting via git history + audience-controlled access + agents reasoning over the substrate + sovereign compute backing them. Three independent rediscoveries is the signal that this is the primitive, not the product.

Tabula captures it once. Each team builds its application; the substrate evolves jointly under OSS governance.

## Scope

| Layer | In Tabula? | Owner |
|---|---|---|
| L0 — Sovereign compute (model registry, privacy-class router, sleep API) | **Yes** | Tabula |
| L1 — Substrate (markdown + git, frontmatter spec, schema vocabulary) | **Yes** | Tabula |
| L2 — Operational log (per-app, sized to ingestion volume) | No | Per-app |
| L3 — Knowledge graph (Graphiti adapter over Postgres+AGE+pgvector) | **Yes** | Tabula |
| L4 — Application logic | No | Per-app |
| ETL adapter framework (L2 → L1 distillation) | **Yes** | Tabula |
| Schema vocabulary (canonical content types) | **Yes** | Tabula |
| Audit + observability (Langfuse hookup) | **Yes** | Tabula |

Not in Tabula: any app's user surface, any app's business logic, any app's brand. Each app keeps full IP at L4. Tabula is a dependency, not a partnership.

## Architecture

### L0 — Sovereign compute

A unified router with three properties:

1. **Privacy-class routing.** Every prompt carries a class. `family_or_self` cannot leave owned infra; `project` may use cloud-OSS (Fireworks, Cerebras, Lambda) but never closed-frontier; `public` may use anything. Class is set per call, defaulted per consumer; misclassification is the security bug.

2. **Sleep API.** GPU-backed models default to cold. The consumer issues `tabula.warm(model, reason, ttl)` to bring up, `tabula.sleep(model)` to release. An idle reaper kills warmed instances on TTL. The router exposes `is_warm` so the application can defer non-urgent calls until the next warm window. **This is the feature Bower's $40/mo economics depend on**; it was an unspoken assumption in TWIN-V1 and Luce that doesn't survive contact with shared infra without being made explicit.

3. **Backend selection.** The model registry is YAML; consumers ask for capability ("classify", "generate", "embed") at a privacy class, and the router picks the cheapest backend that satisfies. Cerebras / Fireworks / Lambda for cloud-OSS; private Claude on GCP for `project`-class frontier needs; owned rig for `family_or_self`. Audit through Langfuse.

### L1 — Substrate

Markdown with YAML frontmatter, stored in git. Tabula owns the *frontmatter spec* and the *schema vocabulary*; consumers own their corpora.

**Schema vocabulary (v1):** vision, person, place, event, project, tool, decision, observation, conversation. Drawn from Bower's content-type taxonomy + Luce's Decision Trace format + TWIN's typed-entity model. Each type has a frontmatter schema (required + optional fields). Adding a type = PR to Tabula.

**Frontmatter spec:** ULID id, timestamps, audience tags, encryption recipients, classification, source provenance, entity references, relations (references / supersedes / caused-by / is-about), tags, release_trigger, redactions, external_ids. Lifted from TWIN-V1 Appendix A.

**Encryption:** age (X25519 + ChaCha20-Poly1305) per-segment, audience-tiered. Frontmatter stays plaintext for queryability.

### L3 — Knowledge graph

Graphiti over PostgreSQL + Apache AGE + pgvector. Hybrid search (semantic + Cypher + full-text), entity reconciliation, MCP tool-call interface for agents.

The graph is *derived*, not authoritative — rebuildable from L1 + L2 on any future infrastructure. Re-indexing is a chore; never a load-bearing data dependency.

### Adapter framework (L2 ↔ L1)

L2 logs raw events (Signal messages, Kitsu API responses, Slack notifications, MCP tool calls). Tabula ships an *adapter framework* — a pattern for distillation pipelines that promote meaningful patterns to typed L1 entries. Each consumer writes its own adapters; the framework handles batching, idempotency, dedup, and audit.

## The sleep mechanism (load-bearing)

Sleep is not an optimization. It's the property that makes Tabula economically viable for $40/mo consumers. Specifying sharply:

| Concept | Definition |
|---|---|
| **Cold** | Backend is not running. Cold-start: 30–90s for big-OSS, 5–15s for small. |
| **Warm** | Backend is loaded, accepting calls. Costs $/hr while warm. |
| **Sticky** | Warm + reserved for one consumer (so concurrent queries don't fight). |
| **Sleep API** | `warm(model, reason, ttl_seconds)` / `sleep(model)` / `is_warm(model)` / `defer_until_warm(callback)`. |
| **Idle reaper** | Background daemon that sleeps any warm model whose last call was > TTL ago. |
| **Coalescing** | If two consumers warm the same model within a window, they share the warm instance. Transparent to callers. |

Bower's concierge (cheap, always-warm) decides whether to wake the heavy agent; the wake call goes through Tabula, which may (a) start a fresh GCP VM, (b) attach to a coalesced warm instance, or (c) defer. The `defer` path is the trick: Bower's UX promises "the agent responds within ~30s," not "instantly," so the concierge buffers messages until the next warm window.

TWIN and Luce use the same API with different defaults. TWIN's recall queries run cheap-warm or against the small local rig; Luce's coordinator runs in batch windows. **The sleep API is universal; the policy is per-consumer.**

## Privacy classes

| Class | Allowed backends | Audit | Default for |
|---|---|---|---|
| `family_or_self` | Owned infra only | Local-only logs | TWIN-personal default; Bower family-tier |
| `project` | Owned + cloud-OSS (Fireworks, Cerebras, Lambda) + private Claude on GCP | Langfuse + local | Luce coordinator; Bower agent calls; TWIN project-tier |
| `public` | Anything including frontier-closed | Langfuse | Public-blog content; benchmarking |

The class is a header on every call. The router refuses to lower the class. Misclassification surfaces as an audit anomaly. The privacy-class invariant is the substrate's strongest claim and its load-bearing one — if it fails, the whole proposition fails.

## Schema vocabulary (canonical content types)

Versioned in `tabula/schema/v1/`. Each type is a frontmatter spec + JSON schema.

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

Luce-specific and Bower-specific types live in extension namespaces, not core. Core stays small.

## Governance and licensing

**License:** Apache 2.0 (explicit patent grant matters given multi-org provenance).

**Steward model:** three-org rotating stewardship initially — @omniscia, Good Studios (Vivake), 2AM Logic (Robb). Each org has commit on substrate; substantive changes require lazy consensus. After year 1, formalize as a foundation or BDFL + council depending on adoption shape.

**IP boundary:** No org assigns IP into Tabula. Each org's L4 application IP is fully its own. Tabula is a dependency, not a partnership; each consumer can fork at any time without entanglement.

**Funding:** Tabula itself is unfunded — work-in-kind from each consuming org. Running infrastructure (e.g., shared reference deployment) is paid by the org using it; the substrate code is free.

## What success looks like (12 months)

- TWIN-personal, Luce, and Bower all running on Tabula for L0/L1/L3
- Sleep API with measured `idle %` ≥ 70% across consumers (validates cost discipline)
- Privacy-class router with zero misclassification incidents in production
- Schema v1 frozen; v2 RFC opened
- ≥ 1 external adopter outside the founding three (validates substrate-not-product framing)
- Reference deployment on Hetzner + GCP, documented well enough that a fourth consumer onboards in a weekend

## Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| **Cost-management wrinkle** | TWIN/Luce demanding 24/7 GPU breaks Bower's $40/mo math | Sleep API is mandatory, not advisory; consumers that won't sleep run on their own L0 instance |
| **Schema drift** | Three orgs each wanting "their" type fragments the vocabulary | RFC process for schema changes; deprecation cycle; v2 starts when ≥2 consumers ask |
| **Governance capture** | One org's roadmap dominates substrate evolution | Steward council, lazy consensus, fork-friendly license |
| **Premature unification** | Building the substrate before any app proves it works | Each consumer builds against its own L0/L1/L3 first; Tabula consolidates only what all three need |
| **Privacy-class invariant failure** | One bad routing decision leaks family content to closed frontier | Default-deny class promotion; explicit audit log; integration tests on every release |

## Phasing

**Phase 0 (now):** Three teams agree. Concept doc ratified. Repo created.

**Phase 1 (Q3 2026):** Bower stands up its own L0/L1/L3 on Tabula primitives. Sleep API gets its first real test (Bower concierge + agent). Schema v1 partially frozen.

**Phase 2 (Q4 2026):** TWIN-personal migrates from current scaffolding to Tabula. Schema v1 fully frozen. First external adopter invited.

**Phase 3 (Q1 2027):** Luce migrates production tracking. Multi-consumer governance pattern stress-tested. Reference deployment documented.

**Phase 4+ (Q2 2027 forward):** OSS launch with public RFC process; v2 schema begins.

## Open questions

1. Where does Tabula live? `github.com/tabula/substrate` (neutral org account, avoids any one stakeholder's branding)?
2. Apache 2.0 confirmed, or argument for MIT?
3. Who pays for the shared reference deployment? Probably the first consumer that needs it (Bower's pilot infra).
4. Curated default model registry, or pure config? Curated default + override seems right.
5. CLI feel: `tabula warm gpt-oss-120b --ttl 1h --reason "luce-batch"`. Confirm.
6. How loud is the "sovereign" framing in copy? Strong framing is the differentiator; not all adopters will care, but it should be load-bearing.

---

*Tabula: the durable surface beneath whatever you build.*
