# Pattern: Audit-overlay composition

> **Origin:** sov-build's SAAP integration with Tabula L0/L1/L3 (V's regulator-defensible audit chain for finance + film deployments).
> **Layers:** Crosscut — hooks at L0, L1, L3, and L4 boundaries.
> **Status:** v1 candidate — proposed via RFC #TBD.

## The problem

Some Tabula consumers need regulator-defensible audit chains:
- Lab49 finance clients need FCA SUP 16, SEC 17a-4, DORA Article 17 evidence packs.
- Luce film deliveries need EU AI Act Article 50 transparency disclosures + C2PA manifests.
- Bower family-tier needs proof that agent actions stayed within consented scope.
- TWIN-personal heir-survivability needs a tamper-evident chain that survives any tool's lifecycle.

Tabula's [`SPEC.md`](../../SPEC.md) says audit is in scope ("Audit + observability (Langfuse hookup)") but stays neutral on the audit protocol — Tabula doesn't pick SAAP, C2PA, custom, or none. This neutrality is correct (different consumers have different audit-protocol requirements), but it leaves a gap: **how does an audit protocol compose with Tabula L0/L1/L3 without redefining substrate primitives?**

Three failure modes when audit is bolted on rather than composed:

1. **Audit-as-separate-stack.** Consumer maintains a parallel audit log alongside L1 substrate. Two sources of truth; can diverge; tampering with one doesn't show up in the other. Regulator can't reconstruct what happened.

2. **Audit-as-substrate.** Consumer redefines L1 to BE the audit chain (every L1 write is an audit event). Couples the audit protocol to substrate; can't swap protocols; Tabula loses its protocol-neutrality.

3. **Audit-without-hooks.** Consumer adds audit emission inside application code (every `chat` call writes an audit event). Brittle — easy to miss a code path; L0/L1/L3 evolutions break audit completeness.

The pattern: **audit protocol is a sibling spec; Tabula provides minimal hook interfaces at layer boundaries; the audit protocol emits via those hooks.**

## The pattern

### Audit protocol as sibling

The audit protocol (SAAP, C2PA, custom, or none) is a separate spec — **not** a Tabula dependency. Tabula consumers pick their audit grade:

- **No audit:** ad-hoc Langfuse traces, no cryptographic chain. Fine for development.
- **Light audit:** Langfuse + cryptographically-signed events on operationally-meaningful actions. Fine for personal use.
- **Heavy audit:** Full SAAP-shaped chain (COSE Sign1, drand anchoring, bi-temporal headers, regulatory mapping). Required for regulated FS, film delivery, family-tier transparency.

The pattern is **protocol-neutral** — it shows where to emit events and the shape of hooks, not the protocol content.

### Hooks at layer boundaries

Tabula provides minimal hook interfaces at each layer boundary:

```
┌─────────────────────────────────────────────────────────────┐
│ L0 — Router emits per inference call                        │
│ Hook: before_call(request, class) + after_call(response)    │
│ Emits: inference_event (model, latency, tokens, decision)   │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│ L1 — Substrate emits per write                              │
│ Hook: before_write(entry) + after_write(commit_sha)         │
│ Emits: content_attribution (writer, audience, classification│
│         git_commit, supersedes_chain)                        │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│ L3 — Graph emits per query and per index update             │
│ Hook: before_query(query, caller) + after_query(results)    │
│ Emits: query_event (query_shape, caller_clearance, result   │
│         count, filtered count)                               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│ L4 — Application emits per agent decision                   │
│ Hook: agent_step(input, output, decision_type)              │
│ Emits: agent_decision (input, options_considered, choice,   │
│         rationale, decision_type, classification)            │
└─────────────────────────────────────────────────────────────┘
```

Each hook is a thin interface: the audit protocol decides what to write; Tabula's layers just invoke the hook at the right boundary.

### Cryptographic chain (audit-protocol-specific)

The pattern doesn't specify the cryptographic shape — that's the audit protocol's concern. But it provides a recommended shape for protocols to follow:

- **Bi-temporal headers**: each event carries `valid_time` (when the event semantically occurred) and `transaction_time` (when it was recorded). Allows "what did the system know at time T?" replay.
- **Hash chain**: each event has `previous_event_hash` pointing to the prior event. Tampering breaks the chain at the modification point.
- **Cryptographic signing**: each event signed with the agent's identity key. Verifiable independently (auditor can verify without trusting the system).
- **Cross-org anchoring**: periodic anchoring to an independent timestamping authority (drand, RFC 3161, blockchain). Defends against substrate-wide rewrites.

SAAP implements all four; lighter protocols may skip some.

### Audit-event taxonomy (recommended)

Audit protocols implementing this pattern emit at least these event types:

- **inference_event** — L0 router call (per request).
- **content_attribution** — L1 write (per entry).
- **agent_decision** — L4 step (per decision; with `decision_type: derived | committed`).
- **policy_event** — Tier-policy binding change or violation.
- **handoff** — Agent-to-agent or human-in-loop transfer.
- **incident_event** — Anomaly detected (misclassification, integrity failure, policy violation).

Optional events:
- **monitoring_attestation** — Periodic "all is well" attestation (required for EU AI Act Article 72 post-market monitoring).
- **system_change_event** — Substrate change with regulatory impact.
- **training_event** — Model training run (provenance for downstream inference attribution).

## Adoption: sov-build's reference implementation

Sov-build wires SAAP (its audit protocol) to all four layer hooks:

- **L0**: Bifrost provider gateway hooks emit SAAP `inference_event` per call.
- **L1**: sovereign-memory facade hooks emit SAAP `content_attribution` per write.
- **L3**: sovereign-memory facade query firewall hooks emit `query_event` per retrieval.
- **L4**: sovereign-agent-runtime emits `agent_decision` per step with `decision_type` derived from the step's role.

SAAP-specific details (COSE Sign1 envelope, drand anchoring, ML-DSA-65 post-quantum migration plan) live in `omniscia/saap` — Tabula doesn't see them. Tabula sees only the hooks.

## Cross-consumer adoption

| Consumer | Audit protocol | Hooks wired |
|---|---|---|
| **Lab49 client deployments** | SAAP (FCA/SEC/DORA evidence packs) | All four layers; full cryptographic chain |
| **Luce** | SAAP + C2PA composition (C2PA at delivery boundary; SAAP through pipeline) | L1 write-hook + L4 agent_decision; C2PA via content_attribution event payloads |
| **Bower** | SAAP-lite (signed events without drand anchoring; family-tier proof of consent scope) | L0 + L4 hooks; light-weight chain |
| **TWIN-personal** | SAAP (heir-survivability; verifiable across machine lifecycles) | All four layers; full chain to support posthumous audit |

The pattern is the same; the protocol grade varies per consumer.

## What it does NOT solve

- **Not the audit protocol itself.** SAAP, C2PA, custom — separate specs. This pattern is the integration shape.
- **Not identity.** Tabula L1's [`identity-model.md`](../../l1/identity-model.md) owns ULID + slug identity. Audit protocols may add cryptographic keys per identity, but the identity model is Tabula's.
- **Not retention policy.** How long audit events live, where they archive, how they delete — out of scope. Each consumer + each audit protocol decides.
- **Not regulator-specific event content.** FCA wants specific fields; SEC wants different ones. Audit protocols map their event types to regulator schemas. Tabula doesn't.
- **Not the audit replay engine.** Reconstructing system state from audit events is a per-protocol concern. SAAP has replay tooling; lighter protocols may not.
- **Not real-time anomaly detection.** Audit emission is one-way (substrate → audit log). Real-time anomaly response is a separate L4 concern.

## Composes with

| Pattern | How |
|---|---|
| [**Decision trace**](decision-trace.md) | `agent_decision` events use decision-trace schema for `options_considered`/`choice`/`rationale` |
| [**Lifecycle vocabulary**](lifecycle-vocabulary.md) | `system_change_event` records `create()`/`clean_up()`/`on_destruct()` for substrate operations |
| [**Tier-policy enforcement**](tier-policy-enforcement.md) | Every tier-policy enforcement decision (L0 route, L1 reject, L3 filter) emits via this pattern |
| [**MoA orchestrator**](moa-orchestrator.md) | Each tier (proposer, aggregator, synthesizer) emits via this pattern |
| [**Sovereign-agent-runtime**](sovereign-agent-runtime.md) | Every agent step emits via this pattern; the runtime is the most prolific emitter |

## Open questions for RFC

1. **Hook interface specification.** Should Tabula publish exact hook signatures (Python typing)? Or stay loose (audit protocol defines its own bindings)? Sov-build's preference: loose at v1, formalize in v2 when ≥2 audit protocols want to compose.

2. **Optional vs mandatory hooks.** Should L1 write-hook be mandatory (every write emits something, even if null)? Or optional (consumers without audit just don't wire it)? Sov-build's preference: optional, but heavy-audit consumers should wire all four for completeness.

3. **Per-tier hook granularity.** L4 agent_decision has a single canonical shape, but L0 inference_event varies by call type (chat vs embed vs classify). Should the pattern enumerate sub-events, or stay at the layer-boundary level?

4. **Cross-protocol composition.** Can a consumer compose SAAP (regulatory audit) + C2PA (content provenance) + Langfuse (operational tracing) at the same hooks? Likely yes (hooks fire multiple subscribers); flagged for clarification.

## Implementation guidance

For a new Tabula consumer adopting this pattern:

1. Pick your audit protocol (SAAP, C2PA, custom, or staged adoption — start with Langfuse, upgrade later).
2. Wire L1 write-hook first — content attribution is the foundational record.
3. Wire L0 router-hook second — inference call traceability.
4. Wire L4 agent_decision-hook third — per-step decision provenance.
5. Wire L3 query-hook last — query traceability (needed for tier-policy enforcement).
6. If using a cryptographic protocol: per-identity key management, anchoring schedule, replay tooling.

Minimum viable adoption: L1 hook only (content attribution). Full adoption: all four hooks + cryptographic chain + cross-org anchoring. Partial adoption is meaningful but advertised as such.

## Cross-references

- Reference implementation: sov-build SAAP integration across `src/app-reference/`
- Audit protocol spec (one example): `omniscia/saap/SPEC.md` (V's SAAP profile)
- L1 substrate invariant: [`l1/substrate-invariant.md`](../../l1/substrate-invariant.md)
- L0 router: [`l0/README.md`](../../l0/README.md)
