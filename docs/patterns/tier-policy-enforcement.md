# Pattern: Tier-policy enforcement

> **Origin:** sov-build's `TIER_POLICY_SPEC.md` + V's Phase 42 update to `SPEC.md` §Privacy classes (data-class vs infrastructure-tier orthogonality).
> **Layers:** L0 + L1 + L3 (multi-layer enforcement).
> **Status:** v1 candidate — proposed via RFC #TBD.

## The problem

Tabula's [`SPEC.md`](../../SPEC.md) defines three data classes — `family_or_self`, `project`, `public` — describing *what the data is, who can see it, what's at stake*. Every commercial deployment also needs an **infrastructure tier** framework — *which backend is approved for which data, in which jurisdiction, under which contract terms*. The two are orthogonal:

- The same `family_or_self` data routes to *sovereign on-prem only* for one customer and *sovereign cloud with ZDR* for another.
- The same `project` data routes to *cloud-OSS* for one customer and *on-prem GPU* for another.

Conflating data class with infrastructure choice produces inflexible policy. Each consumer ends up reinventing the binding shape.

Three failure modes when the dimensions are conflated:

1. **Class-as-backend.** Treating `family_or_self` as "always MLX on Apple Silicon" hardcodes a specific deployment topology into the substrate. Lab49 customers may have a different on-prem fleet; Bower may run on Hetzner; same class, different backend.

2. **Single-policy-per-deployment.** One global rule ("project class always routes to Fireworks") prevents per-customer regulatory binding. Customer A may require Azure UK with ZDR; customer B may require AWS GovCloud — same class, different infrastructure binding.

3. **No enforcement at retrieval.** Write-time classification is necessary but not sufficient. An L3 graph query for "recent decisions" can return class-protected content to a caller who isn't cleared, even when every write was classified correctly. Classification without retrieval-time enforcement leaks.

The pattern: **two orthogonal dimensions composed via per-customer policy binding, enforced at three layer points.**

## The pattern

### Two dimensions

| Dimension | What it classifies | Whose spec |
|---|---|---|
| **Data classification** | Sensitivity, audience, custody | Tabula ([`SPEC.md`](../../SPEC.md) §Privacy classes) — three classes: `family_or_self`, `project`, `public` |
| **Infrastructure tier** | Which backend is approved (model + provider + contract class + jurisdiction) | Implementation choice — each implementation defines its own tier framework |

### Per-customer policy binding

Each customer (or per-(identity, application) account, per Tabula's [identity model](../../l1/identity-model.md)) has a `policy_event` chain recording the binding. The binding follows the [**Decision trace**](decision-trace.md) pattern's schema:

```yaml
---
id: 01HX...                               # ULID
type: decision
kind: policy_event.tier_binding
created_at: 2026-05-14T10:00:00Z
author: person:omniscia
audience: [project, org-acme]
customer: org:acme
class_to_tiers:
  family_or_self: [tier:acme-onprem]
  project: [tier:acme-cloud-zdr, tier:acme-onprem]
  public: [tier:open]
tier_definitions:
  tier:acme-onprem:
    backends: [mlx-acme-rack-1]
    jurisdictions: [GB]
    contract_class: customer-operated
  tier:acme-cloud-zdr:
    backends: [private-claude-azure-uk]
    jurisdictions: [GB]
    contract_class: zero-data-retention
  tier:open:
    backends: [openai-public, anthropic-public]
    contract_class: standard
state: ratified
committers: [person:acme-cto, person:omniscia]
---

Customer ACME's approved binding for Q2 2026. Supersedes 01HW... (previous binding without ZDR Azure tier).
```

Changes to the binding emit new `policy_event` records; previous bindings become `superseded` via the `supersedes` relation. Substrate retains the full chain; auditors can replay any point in time.

### Enforcement at three layer points

```
┌─────────────────────────────────────────────────────────────────┐
│ L0 router enforcement — call-time                               │
│                                                                  │
│ Every prompt carries data class as a header.                    │
│ Router consults customer binding, picks cheapest approved tier, │
│ refuses to lower the class. Misclassification → audit anomaly.  │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│ L1 write enforcement — storage-time                             │
│                                                                  │
│ Every L1 entry's frontmatter MUST carry classification.         │
│ Writer enforces (substrate refuses unclassified writes).        │
│ Reader trusts the field but verifies via realpath/audit.        │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│ L3 retrieval enforcement — query-time (compliance firewall)     │
│                                                                  │
│ Compliance firewall in front of the L3 graph. Query carries     │
│ caller's clearance (per their account's class_to_tiers).        │
│ Firewall filters results BEFORE they return to caller.          │
│ Prevents class-leak via graph search, hybrid search, or         │
│ semantic similarity.                                             │
└─────────────────────────────────────────────────────────────────┘
```

Each enforcement point emits an audit event (per the audit-overlay composition pattern, in progress), making mis-enforcement visible at any layer. A consumer that adopts only one enforcement point (e.g., L0 only) inherits a known gap; adopting all three closes the loop.

## Adoption: sov-build's reference implementation

Sov-build implements all three enforcement points:

- **L0 enforcement** lives in V's Bifrost provider gateway (`src/app-reference/sovereign_provider_register.py`). The customer binding is written as a `policy_event` to the SAAP audit chain; vLLM Semantic Router consults the binding per call and refuses to route `family_or_self` data to non-approved tiers.

- **L1 enforcement** lives in sov-build's sovereign-memory facade (`src/app-reference/sovereign_memory.py`). The facade rejects writes whose frontmatter omits classification or specifies a class the writer's account isn't authorised for.

- **L3 enforcement** lives in the same sovereign-memory facade, which routes Graphiti queries through a compliance-firewall stage. The firewall computes a query-time `allowed_classes` mask from the caller's account and filters results post-graph-retrieval.

Sov-build's specific tier framework is four tiers — Sovereign On-Prem / Sovereign Cloud / Controlled / Open — bound per customer. Other implementations choose their own tier definitions; Tabula owns only the data-class dimension.

## What it does NOT solve

- **Not the data classes themselves.** Tabula [`SPEC.md`](../../SPEC.md) §Privacy classes owns `family_or_self`, `project`, `public`. This pattern is the enforcement architecture, not the class definitions.
- **Not the audit recording of policy events.** The audit-overlay composition pattern (proposed) owns event emission; this pattern emits via that one.
- **Not the policy approval/governance process.** Per-customer ratification (which executives sign, what review cycle) is out of substrate scope.
- **Not jurisdiction-specific regulatory mapping.** Sov-build maps to FCA SUP 16, DORA Article 17, etc.; those bindings live in the implementation, not Tabula.
- **Not the customer identity model.** Tabula L1's [`identity-model.md`](../../l1/identity-model.md) owns customer/person/org slugs (`org:acme`, `person:acme-cto`).
- **Not retroactive reclassification.** Separate concern; supersession via `supersedes` relations per [**Decision trace**](decision-trace.md) pattern.
- **Not classification of NEW writes** (that's an L4 application concern — how does an application know its content is `family_or_self`?). The pattern owns enforcement; classification-assignment is per-app logic.

## Composes with

| Pattern | How |
|---|---|
| [**Dual-tier memory (Path C)**](dual-tier-memory.md) | L2 events carry data class for distillation gating; only correctly-classified events promote to L1 |
| [**Decision trace**](decision-trace.md) | `policy_event.tier_binding` records use the decision-trace schema (options_considered, rationale, supersedes chain) |
| [**Lifecycle vocabulary**](lifecycle-vocabulary.md) | `reset()` rotates policy snapshots quarterly; `clean_up()` enforces tier migration when a backend sunsets |
| **Audit-overlay composition** *(proposed)* | Every enforcement decision (L0 route choice, L1 reject, L3 filter) emits a signed audit event |
| **MoA orchestrator** *(proposed)* | Each proposer's L0 call is tier-checked before routing; ensemble cannot escape policy by parallelism |
| **Sovereign-agent-runtime** *(proposed)* | Agent steps include a canonical tier-policy check stage before any L0 call |

## Open questions for RFC

1. **Tier-framework registry.** Should Tabula provide an optional reference tier-framework registry (so consumers don't reinvent shape), or stay opinion-free? Sov-build's preference: opinion-free (current Tabula stance). Bower may want sov-build's four-tier framework as a starting point.

2. **Cross-implementation policy migration.** When a customer migrates between Tabula implementations (e.g., sov-build → Lab49 productization), how do bindings transfer? Likely out of substrate scope; flagged for follow-up.

3. **Tier-binding schema versioning.** Semantic versioning of binding schema vs `policy_event` content-addressability. Likely the latter (immutable events; new versions emit new records superseding old).

4. **Customer-binding visibility.** Should bindings be readable by the customer (transparency) vs sealed for the implementation (operational privacy)? Likely transparency to the customer's `inner-circle` audience tier; sealed from `public`.

## Implementation guidance

For a new Tabula consumer adopting this pattern:

1. Define your tier framework (a YAML registry of named tiers with backends, jurisdictions, contract classes).
2. Adopt the customer binding shape (`policy_event.tier_binding` per the schema above).
3. Implement L0 enforcement first (router refuses to route across class boundaries) — this is the highest-leverage protection.
4. Implement L1 enforcement second (writer rejects unclassified records) — this prevents accumulation of debt.
5. Implement L3 enforcement last (compliance firewall on graph retrieval) — this closes the retrieval-leak gap.
6. Wire audit emission at each enforcement point per the audit-overlay composition pattern.

Minimum adoption: L0 enforcement only. Full adoption: all three enforcement points + audit. Partial adoption is meaningful but advertised as such (don't claim "tier-policy enforced" without all three).
