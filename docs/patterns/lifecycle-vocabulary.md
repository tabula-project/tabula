# Pattern: Lifecycle vocabulary

> **Origin:** Luce — adapted from LPMud's object lifecycle (Lars Pensjö, 1989).
> **Layers:** All — applies to substrate operations across L0 through L4.
> **Status:** Adopted as the canonical vocabulary for substrate operations. Replaces ad-hoc verbs ("capture", "sync", "archive", "migrate", "sunset") with a precise five-stage model.

## The problem

Substrate operations get described with whatever verb is convenient at the moment: capture, sync, archive, migrate, prune, sunset, retire, deprecate. Different consumers describe the same operation with different verbs; the same consumer describes different operations with the same verb. The vocabulary fragments and the operational shape becomes unclear.

## The pattern

Adopt a five-stage lifecycle, parallel across consumers and layers. The vocabulary comes from LPMud (1989), which gave every object in its world the same lifecycle: `create() → heart_beat() → reset() → clean_up() → on_destruct()`. Luce adopted this for institutional knowledge; Tabula generalizes it to substrate operations broadly.

```
create()        →   heart_beat()   →   reset()        →   clean_up()       →   on_destruct()
new entity         continuous          periodic            tool migration       creator/consumer
arrives            ingestion           archival            (Goldstone)          gone permanently
```

## Stage definitions

### `create()` — entity arrives in the substrate

The first time a record exists. Whether `twin capture`, `bd new`, an L2 distillation promoting an observation, or a manual `git add`, the moment a new ULID gets assigned is `create()`.

```yaml
created_at: 2026-05-04T15:52:00Z   # this is the create() timestamp
```

Properties:
- One create event per record. Even if the record is later edited heavily, `created_at` doesn't move.
- Create events trigger L3 graph updates (entity gets indexed).
- Create events MAY trigger downstream notifications (e.g., a steward should be notified of a `type: decision` create).

### `heart_beat()` — continuous operational pulse

The recurring background activity that keeps the substrate alive and current.

| Layer | What heart_beat does |
|---|---|
| **L0** | Idle reaper checks warm-model TTLs, sleeps expired ones. Coalesces concurrent warm requests. |
| **L1** | Git remote sync (push/pull from co-replicas). Index refresh in derived layers. |
| **L2** | ETL ingestion adapters poll/listen for new events. Distillation pipeline triggers. |
| **L3** | Graphiti incremental updates from L1 commits + L2 events. Vector embedding refresh. |
| **L4** | Application-specific pulse (Bower concierge classifier, Luce shadow-learning, TWIN bot poll). |

heart_beat is the always-on background. If heart_beat stops, the substrate decays: indexes drift, models stay warm and waste money, ingested events pile up undistilled. The phrase "magic going into the ground" (from Luce's OM.md) describes a heart_beat failure: observation is happening but not being incorporated.

### `reset()` — periodic archival rotation

Quarterly (or per-application cadence), the substrate rotates: snapshots get pushed to durable archives (Arweave, Zenodo, Glacier, USB rotation), L2 operational logs may rotate or compress, derived indexes may rebuild from scratch to verify integrity.

reset is **not** deletion. It's the moment the substrate **proves** to itself that the corpus is rebuildable end-to-end. From Luce's OM.md: "letting go of what no longer serves... the field stays alive, not a museum."

| Layer | What reset does |
|---|---|
| **L0** | Model registry refresh; cost telemetry rollup; provider list audit |
| **L1** | Snapshot to durable archives; verify hashes; integrity audit |
| **L2** | Rotate operational logs; compress old data; surface "what could be promoted to L1?" |
| **L3** | Optional: rebuild graph from scratch; verify vs incremental state; reconcile drift |
| **L4** | Application-specific (e.g., Luce: quarterly review of conservation laws; TWIN: family-vision review) |

### `clean_up()` — tool migration / Goldstone migration

When a tool is replaced (deprecated provider, retired service, switched database), `clean_up()` is the controlled extraction of that tool's institutional knowledge before its memory disappears.

The name comes from Luce's "Goldstone migration" pattern (referencing the physics term for a symmetry-broken low-energy state): when a tool's informal workflows would otherwise propagate as institutional amnesia (people remember the workaround, but it's not written down anywhere the next tool sees), `clean_up()` is the deliberate capture of those workflows so the new tool inherits them.

| Trigger | What clean_up does |
|---|---|
| Provider retirement (e.g., Anthropic deprecates a model) | Extract any institutional choices that depend on that model's specific behavior; record as `decision` with `state: superseded` |
| Database migration (e.g., Postgres major version) | Verify L3 rebuild from L1; ensure L2 schema migration captures all event semantics |
| Tool swap (e.g., Kitsu → ShotGrid) | Pull all entities from the old system; reconcile IDs in L2 entity_links; promote orphaned operational state to L1 observations if relevant |
| Schema deprecation in v1 → v2 | Document migration path; deprecation cycle for old fields; rebuild adopters' frontmatter |

### `on_destruct()` — creator / consumer gone permanently

The terminal lifecycle event. The creator dies, the consumer organization dissolves, or the substrate is being formally retired.

This is **not** the routine end-of-session. Sessions are ephemeral; on_destruct is permanent.

| Trigger | What on_destruct does |
|---|---|
| Creator confirmed dead | Heir-access workflow activates: SUCCESSION.md + custodian Shamir keys + Vaultwarden Emergency Access. Audience tiers re-evaluated for posthumous-release content. The substrate transitions to its `successor` operating mode. |
| Consumer org dissolves | The consumer's L2 + L4 retire; their L1 archive may be donated to a foundation, sealed with a release schedule, or merged into a successor consumer's archive (with explicit transfer of stewardship). |
| Substrate retirement | Tabula itself ends. The L1 corpora persist (every consumer keeps theirs); only the shared L0/L1/L3 specs and the Tabula governance retire. By design, consumers are not stranded — their corpora outlive Tabula. |

The phrase from Luce's OM.md: "production wraps. Knowledge persists in the field. The magic doesn't leave. It was incorporated." The substrate's whole point is that on_destruct doesn't lose information; the information was already incorporated through prior `create() → heart_beat() → reset()` cycles.

## Mapping to consumer-specific operations

| Consumer | create() | heart_beat() | reset() | clean_up() | on_destruct() |
|---|---|---|---|---|---|
| **TWIN-personal** | `twin capture` | claude-mem ETL, gt-messaging dual-write | quarterly USB + Arweave + Zenodo | tool deprecation cycle | creator dies → heir-access protocol |
| **Luce** | shadow-learning observation; manual entity creation; ScriptLab ingest | MCP middleware live-feed; Graphiti index updates | quarterly conservation-law review; archival snapshot | provider migration (e.g., Kitsu → next-gen) | studio dissolves; archives donated to film foundation |
| **Bower** | family chat distilled to L1 | concierge classifier; agent-as-peer responses | annual family-vision review | MLS protocol upgrade; agent-model switch | family substrate transitions to family heir-access |

## Why use this vocabulary instead of ad-hoc verbs

1. **Cross-consumer clarity.** When a Bower contributor talks about `reset()`, a Luce contributor knows immediately what shape of operation that is. When someone says "we should sync the corpus", it's ambiguous which lifecycle stage they mean.
2. **Forces operational completeness.** If a consumer hasn't named all five lifecycle stages, they're missing operations they'll eventually need. (Most commonly missing: `clean_up()` — the Goldstone-migration capture before a tool retires.)
3. **Maps cleanly onto agent semantics.** When an agent reasons about substrate operations, "create vs heart_beat vs reset vs clean_up vs on_destruct" gives it five distinct intents to choose from rather than a fuzzy verb-cloud.
4. **Honors the original.** LPMud's lifecycle is one of the few pieces of 1980s software architecture that's still being adopted because it captured something real. Naming our operations after it acknowledges the lineage.

## What this pattern does NOT do

- **It does not enforce timing.** `reset()` is "periodic" but consumer-specific cadence; some consumers reset weekly, others quarterly, others on major-event triggers.
- **It does not constrain what stages each layer must implement.** A consumer with a tiny L2 may have a trivial `heart_beat()`. A long-running L1 may rarely fire `clean_up()`. The vocabulary describes shape; consumers fill it in proportionally.
- **It does not replace the schema.** Lifecycle stages are *operations*; the substrate's typed records (decision/observation/conversation/etc.) are *content*. Both vocabularies coexist.

## Composes with (v1 candidate patterns)

The following v1 candidate patterns adopt this lifecycle vocabulary for their operations:

| Pattern | How it composes |
|---|---|
| [**Sovereign-agent-runtime**](sovereign-agent-runtime.md) | Agents use the full five-stage lifecycle: `create()` (identity + persona load); `heart_beat()` (one step pulse, invoking the canonical 7-stage step sequence); `reset()` (memory snapshot to L1); `clean_up()` (dehydrate via Tabula sleep API); `on_destruct()` (agent retirement with final audit event) |
| [**Tier-policy enforcement**](tier-policy-enforcement.md) | `reset()` rotates per-customer policy snapshots quarterly; `clean_up()` enforces tier migration when an approved backend sunsets (extracts policy-event chain dependencies before the backend disappears) |
| [**MoA orchestrator**](moa-orchestrator.md) | The coordinator agent uses `heart_beat()` for polling proposer responses; `clean_up()` releases coalesced warm instances when the ensemble run completes |
| [**Audit-overlay composition**](audit-overlay-composition.md) | `system_change_event` records (a recommended audit event type) capture `create()`/`clean_up()`/`on_destruct()` lifecycle transitions for substrate operations with regulatory impact |

## Cross-references

- Origin in Luce: `~/gt/luce/crew/vivake/docs/OM.md` (the dharma framing) and `docs/specs/2026-04-01-luce-context-and-learning-architecture.md`
- L1 substrate invariant: [`l1/substrate-invariant.md`](../../l1/substrate-invariant.md)
- Decision-trace pattern (which itself has its own lifecycle states `proposed → ratified → committed → superseded → reversed`): [decision-trace.md](decision-trace.md)
- Dual-tier memory pattern (L2 distillation maps to `heart_beat()`): [dual-tier-memory.md](dual-tier-memory.md)
