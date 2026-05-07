# RFC: `docs/patterns/` vs layer-specific docs — where do reusable patterns live?

**Date:** 2026-05-07
**Author:** Vivake (drafted with Claude Opus 4.7)
**Status:** Open — input requested from @rjwalters and @omniscia
**Affects:** `docs/patterns/`, `etl/path-c-pattern.md`, future cross-layer pattern docs

## The problem

In two days of substrate consolidation we ended up with **two homes for the same pattern**:

- [`docs/patterns/dual-tier-memory.md`](../patterns/dual-tier-memory.md) — written as the canonical "this is the design principle" entry in a cross-layer pattern catalog.
- [`etl/path-c-pattern.md`](../../etl/path-c-pattern.md) — written as the L2↔L1 implementation guide, scoped specifically to ETL adapter authors.

Both source from TWIN-V1 §3.11 ("Path C: working memory + long-term memory"). Both will be edited as the substrate evolves. **They will drift.** And the pattern of "general principle in `docs/patterns/`, layer-specific instance in `<layer>/<name>.md`" will recur — decision-trace, lifecycle vocabulary, audience tiers, every other cross-cutting concern.

If we don't pick a convention now, every future pattern will get re-litigated.

## The three options

### Option A — Keep both. Cross-link.
**`docs/patterns/`** = principle + cross-layer rationale.
**`<layer>/`** = implementation contract for that layer.

Cross-references in both directions. Drift risk handled by editorial discipline.

**Pros:** Two genuinely different audiences (architect reading top-down vs. adapter author reading bottom-up). Link-driven nav.
**Cons:** Drift is real; in 6 months we won't remember which was canonical. Doubles the surface to keep aligned.

### Option B — Layer docs are canonical. `docs/patterns/` becomes an index.
`docs/patterns/dual-tier-memory.md` shrinks to 5 lines: "This pattern operates at L1+L2. See `etl/path-c-pattern.md`."

**Pros:** Single source of truth per pattern. No drift. `docs/patterns/` becomes a thin discovery layer.
**Cons:** Some patterns span layers and don't have a natural "home layer." Lifecycle vocabulary, for example, applies across L0–L4 — no single layer doc fits.

### Option C — `docs/patterns/` is canonical. Layer docs link out.
Every reusable pattern lives in `docs/patterns/`. Layer-specific docs (`etl/README.md`) reference but don't re-explain.

**Pros:** Architects can read `docs/patterns/` as a coherent catalog. One source of truth. Patterns gain visibility — they become Tabula's published vocabulary.
**Cons:** Adapter authors working in `etl/` lose locality — they have to bounce out to `docs/patterns/` to understand the framing. The L1↔L2 specificity gets diluted.

## My recommendation: Option C, with a small twist

Make `docs/patterns/` the canonical home. Each layer's README links to relevant patterns inline ("for the dual-tier pattern this framework implements, see `docs/patterns/dual-tier-memory.md`"), but the layer docs only describe **layer-specific contracts** — not the pattern itself.

The twist: **patterns that are 1:1 with a layer get layer-prefixed names**:
- `docs/patterns/l1-l2-dual-tier-memory.md`
- `docs/patterns/l1-decision-trace.md`
- `docs/patterns/all-layers-lifecycle-vocabulary.md`

This way the catalog is browsable by layer (grep `^l1`) and by topic (full reading), and the layer locality is preserved in the filename.

## Why I lean this way

1. **Tabula's main external artifact is the substrate spec.** People will read `docs/` to understand what we built. Patterns are first-class architecture; they belong with the architecture docs.
2. **Layer code (`etl/`, `l3/`) will increasingly be `pyproject.toml` + Python.** Mixing prose patterns into a Python package's README crowds the implementation surface.
3. **Drift is cheaper to prevent than reconcile.** One canonical location → one edit per change.

## What I'm asking

@rjwalters — does Option C track for you? Specifically:
1. Should patterns be the canonical home, with layer docs deferring? Or do you see Bower needing pattern locality inside `etl/` for any reason I haven't thought of?
2. The naming convention (layer prefix in filename) — useful, or noise?
3. Anything orthogonal — a fourth option I'm missing?

Happy with a "yes, do it" or a counter-proposal. If silent for a few days I'll proceed with Option C as a default and we revisit if anyone hits sharp edges.
