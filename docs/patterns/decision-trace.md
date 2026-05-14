# Pattern: Decision trace

> **Origin:** Luce institutional-learning practice (Good Studios production-tracking platform).
> **Layer:** L1 substrate (canonical type: `decision`, see [`schema/v1/types/decision.yaml`](../../schema/v1/types/decision.yaml)).
> **Status:** Adopted as the canonical structured-decision format across all Tabula consumers.

## The problem

Code shows *what*. Commits show *when*. Neither shows *why*, and crucially, neither shows *why not the alternatives*. Without the alternatives-considered structure, organizational memory of decisions devolves into "we did X" — which loses the most valuable signal: the reasoning that *did not* survive contact with the choice.

Three failure modes when decisions aren't structured:

1. **Implicit decisions.** Choices get baked into code without being recorded as decisions at all. Six months later, someone asks "why did we use R2 instead of B2?" — the answer lives in someone's head or a Slack thread that's been archived.

2. **Single-narrative decisions.** A decision is recorded as "we chose R2" without the comparison. The reader can't tell whether B2 was considered and rejected, or never considered. Future re-evaluation must rebuild the analysis.

3. **Outcome amnesia.** A decision is recorded with rationale, then never revisited. Did it work? Was the rationale sound? The decision sits as a frozen historical claim, never closing the loop to "did we get the outcome we predicted?"

The pattern: **a structured shape that forces capturing the trade-off, not just the choice.**

## The pattern

Every significant decision is recorded as an L1 record with `type: decision` and the canonical schema. The schema is in [`schema/v1/types/decision.yaml`](../../schema/v1/types/decision.yaml); the shape is:

```yaml
---
id: 01HX...                            # ULID
type: decision
created_at: 2026-05-04T15:52:00Z
author: person:omniscia
audience: [project]
decision: |
  One-sentence summary of what changed.
context: |
  What prompted this — the problem or opportunity.
options_considered:
  - id: a
    description: <approach A>
    pros: [...]
    cons: [...]
  - id: b
    description: <approach B>
    pros: [...]
    cons: [...]
  - id: c
    description: <approach C>
    pros: [...]
    cons: [...]
choice: a                              # which option won
rationale: |
  Why this option won. Reference the cons of others where they were decisive.
downstream_effects:
  - "List of bead IDs / file paths affected"
  - "Other decisions that follow from this one"
outcome: null                          # filled in retrospectively
state: ratified                        # proposed | ratified | committed | superseded | reversed
committers: [person:omniscia, person:rjwalters]
---

Free-form notes / discussion / supporting context in the markdown body.
```

The required fields force the trade-off to surface. `options_considered` cannot be a single entry — multiple alternatives are the point. `outcome` starts null and is filled later.

## What counts as a "significant" decision

| Worth a decision record | Just a commit message |
|---|---|
| Tool/vendor selection (R2 over B2; Kitsu over ShotGrid) | Tool config tweak |
| Architecture choices (markdown-corpus + Graphiti vs Neo4j-primary) | Renaming an internal helper |
| Process changes (new task types, new pipeline stages) | Reformatting a doc |
| Personnel decisions (who has access; who's a steward) | Onboarding a single contributor |
| Financial decisions (budget allocation; vendor contracts) | Receipt-keeping |
| Creative decisions (visual approach; editing strategy) | Asset variant naming |
| Substrate-shape decisions (schema additions; layer surface) | Typo fixes |

Heuristic: if you'd want to remember *why* in a year — record it. If you'd be content to git-blame the line and shrug — don't.

## Lifecycle states

```
proposed → ratified → committed → superseded
                                 → reversed
```

- `proposed` — under discussion. Not yet acted on. May still be amended.
- `ratified` — agreed by the relevant committers. Action will follow.
- `committed` — in effect. Code/process/people reflect the choice.
- `superseded` — replaced by a newer decision (which has `relations.supersedes` pointing back).
- `reversed` — rolled back without replacement. The fact that it was reversed *is* historical signal worth preserving.

Decisions never get deleted. The trace is the institutional asset.

## Outcome backfill

Every decision starts with `outcome: null`. As evidence accumulates (months later for a tooling choice; quarters later for a process change; years later for a strategic one), the author or any steward can update the `outcome` field with what actually happened.

```yaml
outcome: |
  6 months in: R2 has saved ~$3.2K/month in egress vs estimated B2 cost.
  Pipeline integration complexity was higher than anticipated (took 4 weeks
  instead of 2). Net assessment: ratified — the egress savings dominate.
```

This loop is what makes the pattern *learning*-positive. Without outcome backfill, decision traces are just a more-structured version of historical claims; with it, they become evidence for the next decision.

## Cross-consumer adoption

| Consumer | How they use the pattern |
|---|---|
| **TWIN-personal** | Personal architectural decisions (substrate moves, tool choices), life decisions worth tracing |
| **Luce** | Production decisions (vendor choice, pipeline stages, creative direction); the original use case |
| **Bower** | Family decisions (move planning, schooling, major purchases); kids accountless but referenced as `entities` |

All three use the same `decision.yaml` schema. Luce-flavored extensions (e.g., `conservation_class: budget` for budget-tracked decisions) are optional fields, not required ones; they don't fragment the core vocabulary.

## What this pattern does NOT solve

- **Routine choices.** Don't burden every commit with a decision trace; the test is "would I want to remember why."
- **Live agent reasoning.** A decision is the *recorded* outcome of reasoning; the reasoning itself happens in conversation, code review, or model context. The trace summarizes; it isn't a chain-of-thought log.
- **Disagreement resolution.** The pattern records the chosen path. If stewards disagree, that's an L4 governance question; the trace can document the disagreement (e.g., "rjwalters dissented; preferred option B for reasons X") but doesn't itself adjudicate.

## Composes with (v1 candidate patterns)

The following v1 candidate patterns emit records that conform to this schema:

| Pattern | How it composes |
|---|---|
| [**Tier-policy enforcement**](tier-policy-enforcement.md) | `policy_event.tier_binding` records (per-customer data-class × infrastructure-tier bindings) use the `type: decision` schema with `options_considered` populated by the reviewed binding alternatives |
| [**Sovereign-agent-runtime**](sovereign-agent-runtime.md) | Step 5 of the canonical agent step emits `agent_decision` events conforming to this schema; `decision_type: derived` vs `committed` distinguishes routine vs commitment-grade steps |
| [**MoA orchestrator**](moa-orchestrator.md) | The synthesizer's final output is a `decision` record with `decision_type: committed`; aggregator votes are populated into `options_considered`; rationale captures the weighted-vote reasoning |
| [**Audit-overlay composition**](audit-overlay-composition.md) | `agent_decision` events emitted by the audit overlay's L4 hook use this schema; the decision-trace pattern is the cross-protocol-portable shape for committed-decision events |

## Cross-references

- Schema: [`schema/v1/types/decision.yaml`](../../schema/v1/types/decision.yaml)
- L1 frontmatter base: [`l1/frontmatter-spec.md`](../../l1/frontmatter-spec.md)
- Lifecycle vocabulary (which extends to decision states): [lifecycle-vocabulary.md](lifecycle-vocabulary.md)
- Origin in Luce: `~/gt/luce/crew/vivake/docs/specs/2026-04-01-luce-context-and-learning-architecture.md` §5
