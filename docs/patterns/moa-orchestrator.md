# Pattern: Mixture-of-Agents (MoA) orchestrator

> **Origin:** sov-build's `saap_moa.py` + `saap_moa_service.py` (V's reference implementation for high-stakes ensemble inference).
> **Layers:** L0 (router + sleep API) + L4 (composition logic).
> **Status:** v1 candidate — proposed via RFC #TBD.

## The problem

For high-stakes decisions — a regulated trade, a committed creative direction, a safety-critical family-tier response — single-model output carries unbounded variance. Even a top-tier model produces inconsistent answers across runs, and on novel problems it may produce a plausibly-confident wrong answer. Ensemble inference (multiple models, weighted reconciliation, final synthesis) reduces variance and surfaces dissent.

Three failure modes when ensemble inference is reinvented per consumer:

1. **Single-model-with-retries.** A consumer treats variance as a "try again" problem; same model called N times. This catches some flakiness but doesn't surface model-specific blind spots. Same model = same biases.

2. **Voting-without-rationale.** N models propose; majority wins. No record of which alternatives lost or why. The losing votes carry signal the winner doesn't — model A may have been right and outvoted; without rationale, the loss is invisible.

3. **Hardcoded model panels.** A consumer hardcodes "always Claude + GPT + DeepSeek" without consulting privacy class. Family-tier data routes to public-frontier; classification leak.

The pattern: **N proposers → M aggregators → 1 synthesizer, all routed through Tabula L0, all emitting audit events.**

## The pattern

### Three-tier composition

```
                  Proposers (N)                Aggregators (M)             Synthesizer (1)
                  ─────────────                ───────────────             ───────────────
input prompt  →   each model gets       →     each receives all       →   one final model
                  the same prompt;            N proposals; emits           emits the chosen
                  emits candidate +           a weighted vote +            response with
                  confidence + rationale      reasoning + confidence       full rationale
                       │                            │                            │
                       ▼                            ▼                            ▼
                  L0 router routes            L0 router routes             L0 router routes
                  to N approved               to M approved                to 1 approved
                  backends (parallel)         backends (parallel)          backend
                       │                            │                            │
                       ▼                            ▼                            ▼
                  Audit event per             Audit event per              Audit event with
                  proposer call               aggregator decision          decision_type =
                                                                           committed
```

### Each tier's responsibilities

**Proposers (N, default N=3):** Independent models that each receive the same input and emit a candidate response with a confidence score. Independence matters — different model families (Claude, GPT, DeepSeek-V4, local Qwen) produce different biases; same-family duplicates don't.

**Aggregators (M, default M=2):** Models that see all N proposals and emit a weighted vote plus reasoning. Aggregators may be smaller/cheaper than synthesizers — their job is to spot which proposals are coherent and which are off. M > 1 catches aggregator bias.

**Synthesizer (1):** One model (typically the most capable available for the privacy class) emits the final response, conditioned on the aggregators' votes + reasoning. The synthesizer commits — its output carries `decision_type: committed` per the [**Decision trace**](decision-trace.md) pattern.

### Tier-policy and Sleep API integration

All MoA calls route through Tabula L0:
- Each call carries data class as a header.
- L0 enforces tier-policy per the [**Tier-policy enforcement**](tier-policy-enforcement.md) pattern (no proposer call escapes policy by parallelism).
- L0's sleep API coordinates: proposer + aggregator + synthesizer models may share a coalesced warm instance if they're on the same backend; cold-start latency is amortized.

### Confidence thresholds and fallback

Each tier's output carries a confidence score in [0, 1]. Application-defined thresholds determine flow:

- **Proposer disagreement** (max confidence < threshold OR proposal divergence high): escalate to a deeper synthesizer pass with explicit "model A says X; model B says Y" context.
- **Aggregator dissent** (M aggregators split): defer to human review per Decision trace's `escalation` event.
- **Synthesizer low-confidence** (< threshold): mark the decision `proposed` rather than `committed`; surface for review.

## Adoption: sov-build's reference implementation

Sov-build's `src/app-reference/saap_moa.py`:

- **N=3 proposers** by default: Claude Sonnet (Anthropic), GPT-4o-mini (OpenAI), DeepSeek-V4 (local MLX) — different model families.
- **M=2 aggregators**: Claude Opus + private Claude on Azure (project-class consumers; falls back to one Claude Sonnet aggregator for family-tier).
- **1 synthesizer**: Claude Opus (or private Claude for project-class).
- **Weighted voting**: confidence-weighted; aggregator agreement increases synthesizer trust threshold.
- **SAAP audit emission** per stage: `inference_event` per proposer, `agent_decision` per aggregator (with `decision_type: derived`), `agent_decision` per synthesizer (with `decision_type: committed`).

Sov-build's `saap_moa_service.py` exposes this as a FastAPI service consumable by the sovereign-agent-runtime.

## Cross-consumer adoption

| Consumer | Where MoA fits |
|---|---|
| **TWIN-personal** | Deep-reasoning lane (multi-model self-consistency for important personal decisions; book-project research synthesis) |
| **Luce** | Committed-decision gate (Director × DP × VFX-supervisor agent ensemble for shot direction; production-bible commitments) |
| **Bower** | Safety-critical responses for family-tier (anything affecting children, health, legal — ensemble before commit) |

All three benefit from the same composition shape; per-consumer parameters (N, M, model panels, thresholds) vary.

## What it does NOT solve

- **Not routing.** Tabula L0 router does that. MoA calls L0; L0 picks the backend.
- **Not agent-step semantics.** [**Sovereign-agent-runtime**](sovereign-agent-runtime.md) pattern owns the canonical step loop. MoA is one technique a step might use.
- **Not audit recording.** [**Audit-overlay composition**](audit-overlay-composition.md) pattern owns event emission; MoA emits via that one.
- **Not tier-policy enforcement.** [**Tier-policy enforcement**](tier-policy-enforcement.md) owns the data-class × infrastructure-tier model; MoA proposers + aggregators + synthesizer all route through that enforcement.
- **Not the prompt itself.** Per-application logic — MoA is the orchestration shape, not the content.
- **Not real-time ensembling.** Default flow is sequential (proposers → aggregators → synthesizer). Real-time streaming aggregation is a separate concern (out of scope).
- **Not training-based ensembling** (LatentMAS, RecursiveMAS-style trained ensembles). This pattern is training-free composition of off-the-shelf models.

## Composes with

| Pattern | How |
|---|---|
| [**Decision trace**](decision-trace.md) | The synthesizer's final output is a `decision` record with `decision_type: committed`; aggregator votes are recorded as `options_considered` |
| [**Lifecycle vocabulary**](lifecycle-vocabulary.md) | MoA's coordinator uses `heart_beat()` for polling proposer responses; `clean_up()` releases coalesced warm instances |
| [**Tier-policy enforcement**](tier-policy-enforcement.md) | Each call (proposer, aggregator, synthesizer) is tier-checked at L0 before routing |
| [**Audit-overlay composition**](audit-overlay-composition.md) | All stages emit signed audit events: `inference_event` per call, `agent_decision` per aggregator/synthesizer |
| [**Sovereign-agent-runtime**](sovereign-agent-runtime.md) | An agent step that needs ensemble inference calls MoA; the runtime owns the step semantics, MoA owns the composition |

## Open questions for RFC

1. **Default N and M.** Sov-build defaults to N=3, M=2. Is this right for all consumers? Bower (cost-sensitive) may prefer N=2; Luce (high-stakes) may prefer N=5. Per-consumer or Tabula default?

2. **Independence enforcement.** Should Tabula's MoA pattern require proposers from different model families? Sov-build enforces this in the YAML config; could be soft (recommendation) or hard (validation rule).

3. **Streaming aggregation.** Real-time streaming-as-it-arrives aggregation is appealing for UX (Bower wants <30s response). Out of scope for v1; flagged as v2 candidate.

4. **Failure modes.** When proposers disagree wildly or aggregators split, what's the canonical fallback? Sov-build defers to human; Bower may defer to cheap-but-conservative; Luce may demand consensus. Per-consumer policy or Tabula default?

## Implementation guidance

For a new Tabula consumer adopting this pattern:

1. Pick N proposers (≥2; default 3) from different model families. Use Tabula's L0 backend selection — don't hardcode model names.
2. Pick M aggregators (≥1; default 2). Aggregators see ALL proposals — give them combined context.
3. Pick 1 synthesizer. Synthesizer commits — its output is the decision record.
4. Wire each call through L0 router (privacy-class header).
5. Wire audit emission at each stage per **Audit-overlay composition**.
6. Define confidence thresholds per your application's tolerance for variance.
7. Define fallback behavior on disagreement.

Minimum viable adoption: N=2 proposers, M=1 aggregator, 1 synthesizer. Full adoption: N=3-5, M=2, configurable fallback. Partial adoption (e.g., no audit, no tier-policy) is meaningful but advertised as such.

## Cross-references

- Reference implementation: sov-build `src/app-reference/saap_moa.py` + `saap_moa_service.py`
- L0 router design: [`l0/README.md`](../../l0/README.md) + [`l0/docs/router.md`](../../l0/docs/router.md)
- Sleep API: [`SPEC.md`](../../SPEC.md) §The sleep mechanism (load-bearing)
- Related research (informing future v2): LatentMAS (arXiv:2511.20639), RecursiveMAS (arXiv:2604.25917)
