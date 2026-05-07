# Tabula

**A sovereign-AI common core: privacy-class routing, cold-by-default compute, decadal substrate.**

> *Tabula: the durable surface beneath whatever you build.*

Tabula is open-source infrastructure shared by a small set of independent applications — TWIN, Luce, Bower, and others — that each need the same primitives: sovereign-class compute, durable typed memory, and a knowledge graph derived from both. Rather than each project rebuilding its own, Tabula is the substrate they jointly depend on.

**Status:** Concept stage (May 2026). Spec ratification pending.

## Reading order

- **[SPEC.md](SPEC.md)** — full architecture and design intent
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — how decisions are made; the steward + RFC model
- **[docs/five-layer-architecture.md](docs/five-layer-architecture.md)** — the L0–L4 substrate model (shared vs per-app)
- **[docs/deployment.md](docs/deployment.md)** — OSS-first reference stack; bootstrap timing
- **[docs/deployments-pattern.md](docs/deployments-pattern.md)** — multi-archive deployment pattern
- **[docs/patterns/](docs/patterns/)** — reusable cross-layer patterns (dual-tier memory, decision trace, lifecycle vocabulary)
- **[l0/](l0/)** — sovereign compute: model registry, privacy-class routing, sleep API, router
- **[l1/](l1/)** — substrate: frontmatter spec, identity model, encryption, replication, capture/recall
- **[l3/](l3/)** — knowledge graph: Postgres + Apache AGE + pgvector + Graphiti
- **[etl/](etl/)** — L2 → L1 distillation framework + Path C reference pattern
- **[schema/](schema/)** — canonical content-type vocabulary (v1 in progress)

## Stewards (proposed)

- @omniscia — TWIN-personal
- Vivake / Good Studios — Luce
- @rjwalters / 2AM Logic — Bower

## License

[Apache 2.0](LICENSE).
