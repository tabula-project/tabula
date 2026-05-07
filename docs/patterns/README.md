# Tabula patterns

Reusable architectural patterns that consumers (TWIN, Luce, Bower, future) implement on top of the substrate. Each pattern names a specific shape of problem and the convention for solving it within the five-layer architecture.

## Patterns in v1

| Pattern | Origin | Layer(s) | Used by |
|---|---|---|---|
| [Dual-tier memory (Path C)](dual-tier-memory.md) | TWIN gt-messaging | L1 + L2 | All consumers with high-volume ingestion |
| [Decision trace](decision-trace.md) | Luce institutional learning | L1 | All consumers; especially governance-heavy ones |
| [Lifecycle vocabulary](lifecycle-vocabulary.md) | Luce (LPMud-derived) | All | Substrate operations across all layers |

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
