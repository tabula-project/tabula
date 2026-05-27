# Tabula patterns

Reusable architectural patterns that consumers (TWIN, Luce, Bower, future) implement on top of the substrate. Each pattern names a specific shape of problem and the convention for solving it within the five-layer architecture.

## Patterns in v1 (ratified)

| Pattern | Origin | Layer(s) | Used by |
|---|---|---|---|
| [Dual-tier memory (Path C)](dual-tier-memory.md) | TWIN gt-messaging | L1 + L2 | All consumers with high-volume ingestion |
| [Decision trace](decision-trace.md) | Luce institutional learning | L1 | All consumers; especially governance-heavy ones |
| [Lifecycle vocabulary](lifecycle-vocabulary.md) | Luce (LPMud-derived) | All | Substrate operations across all layers |

## v1 candidates (proposed; pending RFC)

The following patterns are proposed for v1 inclusion via the RFC + lazy-consensus process (see `CONTRIBUTING.md`). They originate in sov-build (V's Tabula reference implementation) and target multi-consumer adoption.

| Pattern | Origin | Layer(s) | Multi-consumer fit |
|---|---|---|---|
| [Tier-policy enforcement](tier-policy-enforcement.md) | sov-build (V's Phase 42 + TIER_POLICY_SPEC) | L0 + L1 + L3 | Lab49 (per-customer regulatory binding); Luce (production-tier vs internal-tier); Bower (family vs project); TWIN (per-archive tiers) |
| [Mixture-of-Agents (MoA) orchestrator](moa-orchestrator.md) | sov-build (`saap_moa.py`) | L0 + L4 | TWIN deep-reasoning; Luce committed-decision gate; Bower safety-critical responses; Lab49 high-stakes finance |
| [Audit-overlay composition](audit-overlay-composition.md) | sov-build SAAP integration | crosscut (L0, L1, L3, L4 hooks) | Lab49 FCA/SEC/DORA evidence; Luce EU AI Act + C2PA; Bower family-tier audit; TWIN heir-survivability |
| [Sovereign-agent-runtime](sovereign-agent-runtime.md) | sov-build (`sovereign_agent.py`) | L4 over L0+L1+L3 | Luce 9-agent constellation; Bower family agent; TWIN persona-grounded bot; V_BRAIN cross-persona orchestration |

The four candidate patterns are MECE among themselves and with the three ratified patterns. Each owns exactly one concern; cross-composition is explicit in each pattern doc's "Composes with" section.

## Why patterns

The five-layer architecture defines *where* things live. Patterns define *how* common problems are solved within that structure. Three independently-conceived applications converged on the same primitives; we can also assume they'll converge on the same recurring problems. Naming and documenting the patterns lets each consumer adopt the same shape rather than reinventing.

## Adding a pattern

A pattern lands in this directory when:

1. At least two consumers face the same problem and arrive at structurally similar solutions, OR
2. A specific consumer documents a solution generic enough that other consumers should adopt it before they reinvent.

Use the existing pattern docs as templates. Each pattern doc should answer:

- What problem does it solve?
- Where in the five-layer architecture does it operate?
- What's the canonical implementation shape?
- How do consumers adopt it (concrete example for one consumer)?
- What does it NOT solve (so the boundary is clear)?
