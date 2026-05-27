# Pattern: Sovereign-agent-runtime

> **Origin:** sov-build's `sovereign_agent.py` (V's "Virtual TD" — the SAAP-audited per-step agent runtime).
> **Layers:** L4 (over L0 + L1 + L3).
> **Status:** v1 candidate — proposed via RFC #TBD.

## The problem

Building an agent loop that integrates Tabula primitives is repetitive. Every L4 consumer needs to: route inference through L0 (privacy-class aware), record observations to L1 (typed entries), query L3 for relevant context (with compliance filtering), emit audit events (per audit-overlay), enforce tier policy (per tier-policy enforcement). Each consumer reinvents the step semantics; subtle bugs (forgetting to emit an audit event on a fallback path; querying L3 without tier-policy check) creep in.

Three failure modes when agent runtimes are reinvented:

1. **Step-without-recording.** Agent calls L0, gets a response, returns — no L1 observation. Substrate doesn't accumulate institutional memory. The agent forgets its own work.

2. **Recording-without-classification.** Agent writes to L1 without classification frontmatter. Downstream queries can't enforce tier policy. Substrate accumulates unclassified debt.

3. **Routing-without-tier-check.** Agent calls L0 without explicit class header. Router defaults to permissive class; class leak.

The pattern: **a canonical step loop that integrates all four substrate concerns at well-defined points.**

## The pattern

### Canonical step semantics

Every agent step is a sequence of stages:

```
┌──────────────────────────────────────────────────────────────┐
│ 1. classify_input                                            │
│    Determine the data class of the incoming input.           │
│    Per-app logic; default: inherit from prior context.       │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│ 2. retrieve_context (L3 query)                               │
│    Query L3 graph for relevant prior records.                │
│    Compliance firewall (per Tier-policy enforcement)         │
│    filters results to caller's clearance.                    │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│ 3. tier_check (per Tier-policy enforcement)                  │
│    Confirm the call's class × tier binding is approved       │
│    before routing. Refuse if not authorized.                 │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│ 4. route_and_execute (L0 router)                             │
│    L0 picks the cheapest approved backend for the class.     │
│    Sleep API: defer_until_warm if non-urgent and not warm.   │
│    Single call OR ensemble (per MoA orchestrator).           │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│ 5. emit_audit (per Audit-overlay composition)                │
│    Emit inference_event for the L0 call.                     │
│    Emit agent_decision for the step's choice.                │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│ 6. record_observation (L1 write)                             │
│    Write a typed L1 entry capturing the step.                │
│    Schema: type=observation, with frontmatter classification.│
│    Emits content_attribution (per Audit-overlay).            │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│ 7. transition                                                │
│    Compute next-step context. Return control to scheduler.   │
└──────────────────────────────────────────────────────────────┘
```

### Lifecycle integration

The runtime uses [**Lifecycle vocabulary**](lifecycle-vocabulary.md) stages:

- **`create()`** — Agent identity assigned (Tabula slug or SAID for SAAP-using consumers); persona loaded; initial L1 archive deployed.
- **`heart_beat()`** — One step pulse. Each step invokes the 7-stage sequence above.
- **`reset()`** — Memory snapshot. State serialized to L1; ready for dehydrate.
- **`clean_up()`** — Dehydrate via Tabula L0 sleep API. Agent state preserved in L1; warm instance released.
- **`on_destruct()`** — Agent retired permanently. Final audit event; archive sealed.

The `reset()` + `clean_up()` integration with Tabula's sleep API is what makes cold-by-default agents economically viable: the agent can suspend mid-conversation, snapshot to L1, release the warm instance, and rehydrate later from the L1 snapshot.

### Agent identity

Each agent has an identity slug per Tabula's [`identity-model.md`](../../l1/identity-model.md) (or SAID format for protocols that adopt SAAP). Identity carries:
- Audience tier authorization (which L1 audiences the agent can read/write).
- Tier-policy binding (which infrastructure tiers the agent can route to).
- Audit-protocol signing key (if the audit protocol requires per-agent keys).

### Memory-snapshot for sleep

The runtime's `clean_up()` stage writes a memory-snapshot L1 record:

```yaml
---
id: 01HX...
type: agent_memory_snapshot
created_at: 2026-05-14T15:00:00Z
agent: agent:luce:studio-brain
audience: [org-goodstudios]
state:
  current_step: 47
  context_window_summary: <distilled summary of prior 46 steps>
  open_questions: [...]
  pending_handoffs: [...]
supersedes: 01HW...  # previous snapshot
---
```

Rehydration: the runtime loads the most recent `agent_memory_snapshot` for the agent's identity; resumes from `current_step + 1`.

## Adoption: sov-build's reference implementation

Sov-build's `src/app-reference/sovereign_agent.py`:

- **Agent class**: holds identity (SAID), persona (from PERSONA.md), audience tier authorization.
- **AgentStep**: the 7-stage sequence above, implemented as Python methods. Each stage is overridable per agent (Luce's Studio Brain has a different `classify_input` than V_BRAIN's correspondence-drafter).
- **AgentRunner**: the executor. Wraps `heart_beat()` calls; integrates with Tabula's sleep API for cold-by-default.
- **SAAP integration**: each stage emits the relevant SAAP event via `saap_provenance.py` (per Audit-overlay composition).
- **MoA integration**: when `route_and_execute` needs ensemble inference, calls `saap_moa.py` (per MoA orchestrator).
- **Memory snapshot**: implemented as L1 `type: agent_memory_snapshot` records with chacha20-poly1305 encryption per audience tier.

Used by: V_BRAIN agent stack, BTX, Shamrock, book-project agent, plus the agents in Luce/Eve/PII via Good Studios's adoption.

## Cross-consumer adoption

| Consumer | Agent stack | Per-agent overrides |
|---|---|---|
| **TWIN-personal** | V_BRAIN orchestrator + BTX + Shamrock + book-project + scheduling + correspondence-drafter | Each agent inherits the 7-stage step; `classify_input` overridden per agent's domain |
| **Luce** | 9-agent constellation (Studio Brain + 4 doublets) | Doublet pairs override stage 5 (`emit_audit`) to emit paired-agent reconciliation events |
| **Bower** | family-tier concierge + rooms agents | Concierge agent has a "fast path" that skips stages 2 + 6 for trivial queries |
| **Lab49 client deployments** | per-customer trading + monitoring agents | All 7 stages mandatory; regulator-defensible per-step audit |

## What it does NOT solve

- **Not the agent's content.** What the agent actually says/does in `route_and_execute` is per-application logic. The pattern owns the step semantics, not the agent's intelligence.
- **Not the persona.** PERSONA.md grounding is per-application; agent runtime just loads it.
- **Not ensemble composition.** [**MoA orchestrator**](moa-orchestrator.md) owns that; the runtime calls MoA when needed.
- **Not tier-policy rules.** [**Tier-policy enforcement**](tier-policy-enforcement.md) owns the model; the runtime invokes the check.
- **Not audit recording.** [**Audit-overlay composition**](audit-overlay-composition.md) owns event emission; the runtime emits via hooks.
- **Not scheduling.** When agents run, in what order, on which machines — per-deployment concern.
- **Not agent-to-agent handoff protocol.** Handoff events emit per audit-overlay, but the handoff coordination logic (who picks up next, what context transfers) is per-application.

## Composes with

| Pattern | How |
|---|---|
| [**Decision trace**](decision-trace.md) | Step 5 (`emit_audit`) emits `agent_decision` events conforming to decision-trace schema |
| [**Lifecycle vocabulary**](lifecycle-vocabulary.md) | The runtime uses `create()` / `heart_beat()` / `reset()` / `clean_up()` / `on_destruct()` for its lifecycle |
| [**Dual-tier memory (Path C)**](dual-tier-memory.md) | Step 6 (`record_observation`) writes to L1; L2 ops-log captures step transitions; distillation pipeline promotes meaningful patterns |
| [**Tier-policy enforcement**](tier-policy-enforcement.md) | Step 3 (`tier_check`) invokes the policy enforcement before routing; Step 2 (`retrieve_context`) invokes L3 compliance firewall |
| [**Audit-overlay composition**](audit-overlay-composition.md) | Step 5 (`emit_audit`) is the agent's primary audit emitter |
| [**MoA orchestrator**](moa-orchestrator.md) | Step 4 (`route_and_execute`) may invoke MoA for high-stakes decisions |

## Open questions for RFC

1. **Step granularity.** Sov-build's 7-stage step is comprehensive but heavyweight (every step does L3 query + L1 write). Lightweight agents (Bower concierge classifier) may want a "fast path" that skips L3 query for routine cases. Configurable per-agent or per-call?

2. **Multi-step transactions.** Some applications need atomicity across multiple steps (e.g., Luce's "commit budget revision" affects 3 records). Should the runtime have a transaction primitive? Currently per-app.

3. **Async / concurrent steps.** Can an agent execute multiple `heart_beat()`s concurrently (parallel reasoning lanes)? Sov-build's current model is sequential per agent; concurrent across agents.

4. **Memory-snapshot frequency.** Snapshot on every `clean_up()` is expensive for long-running agents. Should there be a "checkpoint" mid-run? Per-app or runtime-managed?

## Implementation guidance

For a new Tabula consumer adopting this pattern:

1. Define your agent identity scheme (Tabula slug or SAID).
2. Implement the 7-stage step loop. Stages 1-4 are app-specific; stages 5-7 can use library code.
3. Wire audit emission per **Audit-overlay composition** (Steps 5 + 6).
4. Wire tier-policy enforcement per **Tier-policy enforcement** (Step 3 + L3 firewall in Step 2).
5. Wire MoA per **MoA orchestrator** for high-stakes steps (Step 4 substitute).
6. Implement memory-snapshot for sleep integration (Step 6 special-case + `clean_up()` stage).
7. Define your application-specific overrides for stages 1-4.

Minimum viable adoption: 4 stages (classify → route → audit → record). Full adoption: all 7 stages + lifecycle integration + sleep API + memory-snapshot. Partial adoption is meaningful but advertised as such.

## Cross-references

- Reference implementation: sov-build `src/app-reference/sovereign_agent.py`
- L1 identity model: [`l1/identity-model.md`](../../l1/identity-model.md)
- L0 router + sleep API: [`l0/README.md`](../../l0/README.md), [`SPEC.md`](../../SPEC.md) §The sleep mechanism
- Persona pattern (per-application; out of scope here): TWIN-V1 §3.6
