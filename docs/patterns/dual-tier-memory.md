# Pattern: Dual-tier memory (working + long-term)

> **Origin:** TWIN gt-messaging plugin convergence (originally documented as "Path C" in TWIN-V1.md §3.11).
> **Layers:** L1 substrate + L2 operational log.
> **Status:** Adopted as the canonical L1 ↔ L2 pattern for any Tabula consumer with high-volume ingestion.

## The problem

A consuming application (Bower's chat layer, Luce's MCP middleware, TWIN's ingestion adapters) receives a high-volume stream of events: Signal/Telegram/Slack messages, Kitsu API responses, calendar events, file watches, MCP tool calls. Most events are operationally meaningful but historically forgettable. A few carry durable signal worth keeping for years.

Two failure modes if the boundary is unclear:

1. **All-into-L1 (over-conservative).** Every event becomes a markdown file in the L1 corpus. Corpus inflates to millions of files in a year. ULID searches slow down. The "second time better than first" insight collapses under the noise — the durable record is buried.

2. **All-into-L2 (over-aggressive).** All events live in the operational log. When Postgres dies or rotates, durable institutional memory dies with it. The substrate-outlives-any-tool guarantee fails. Heir-readability fails.

The pattern: **two tiers, neither all of one nor all of the other.**

## The pattern

Mirror the working-memory / long-term-memory split in human cognition.

```
┌─────────────────────────────────────────────────────────┐
│  Working memory (L2 — operational log)                  │
│  Tactical state: session context, recent buffer,        │
│  dedupe, scrubbing pipeline, conversational history     │
│  Storage: SQLite / Postgres / Parquet (per-app sized)   │
│  Lifetime: hours to weeks; can be wiped without loss    │
└──────────────────────┬──────────────────────────────────┘
                       │ distillation pipeline
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Long-term memory (L1 — substrate)                      │
│  Durable record: typed entities, decisions,             │
│  observations, distilled patterns, relationships        │
│  Storage: markdown + git, decadally durable             │
│  Lifetime: indefinite; the canonical institutional      │
│  record                                                 │
└─────────────────────────────────────────────────────────┘
```

Every ingested event lands in L2 first. A distillation pipeline runs (continuously, on-schedule, or on-demand) and promotes meaningful patterns to L1 as typed entities (`type: observation`, `type: decision`, `type: conversation`, etc.). The L2 layer can be wiped, rotated, or migrated without losing anything in L1.

## Sizing per consumer

L2 volume is application-specific:

| Consumer | L2 volume | Storage |
|---|---|---|
| Bower (family chat + calendar) | Light — ~thousands of events/year | SQLite |
| TWIN-personal (Signal/Telegram/email/calendar/voice) | Medium — ~tens of thousands/year | SQLite |
| Luce (Kitsu+ClickUp+Slack+R2+Frame.io+MCP middleware) | Heavy — millions/year | Postgres |

L1 volume is roughly the same shape across consumers because distillation reduces by 100×–1000×. A heavy-ingestion app produces only modestly more L1 entries than a light one — the noise stays in L2.

## Implementation: gt-messaging (TWIN's first instance)

TWIN's gt-messaging plugin (`omniscia/twin/messaging/`) is the reference implementation of this pattern.

### L2 storage
- `~/gt/.runtime/messaging/messages.db` — SQLite, all conversations + messages, append-only
- `~/gt/.runtime/messaging/conversations.db` — bot conversational state (LiteLLM threads)
- `~/gt/.runtime/messaging/sessions/*` — Telethon/signal-cli sessions (mode 600)

### L1 distillation target
- For Signal/Telegram messages distilled into a coherent conversation: `type: conversation` in TWIN's L1 archive
- For decisions made in chat (e.g., naming Tabula): `type: decision`
- For ad-hoc observations or commitments: `type: observation`

### Audience-tier mapping
Per-conversation classification in `messaging/settings.json`:

```json
{
  "audience_map": {
    "sg:contact:+16264834952": ["project"],
    "tg:robb-florence": ["family", "project"],
    "sg:contact:+1...": ["self"]
  }
}
```

Unmapped conversations default to `[self]` and surface for human classification on first read.

### Distillation triggers
- **Message arrival** → write to L2 immediately
- **Topic shift** (gap > 4h, channel close) → schedule conversation distillation
- **Explicit decision marker** (e.g., overseer says "lock this in") → immediate decision-record promotion
- **Periodic (daily)** — sweep L2 for emergent patterns; promote multi-message threads as `type: observation` summaries

### What L2 keeps that L1 never sees
- Session keys (Telethon sessions, signal-cli identity material)
- Message-receipt acknowledgments
- Per-message scrubbing pipeline state
- Failed-send retry queues
- LiteLLM conversational context windows
- Bot conversational history beyond what was distilled

These are operational; they belong to L2 alone.

## Implementation: Luce shadow-learning (the heavy case)

Luce's shadow-learning middleware (FastMCP coupling-weight pattern) is the same pattern at higher volume.

### L2 storage
- Postgres: `shadow_observations` table — every MCP tool call (kitsu/clickup/slack/frame.io/drive)
- Coupling weight per tool determines processing depth (1.0 = capture everything; 0.3 = event-level only)

### L1 distillation target
- Decision events from Slack canvases → `type: decision` records with the canonical Decision Trace shape
- Pattern recognition (e.g., "Day 3 shots have 2x retake rate") → `type: observation` records with `pattern_count`
- Production status changes → contributing to `production:eve` entity record updates

### What L2 keeps that L1 never sees
- Raw HTTP request/response payloads (audit-only)
- Tool latency telemetry
- Coupling weight evolution
- API rate-limit accounting

## Implementation: Bower concierge classifier (the light case)

Bower's concierge (always-warm, ~$5/mo) decides per-message whether to wake the heavy agent.

### L2 storage
- SQLite per family: encrypted message buffer, classifier decisions, agent-wake history
- Tiny: family chat is ~tens of messages/day, not thousands

### L1 distillation target
- Family decisions → `type: decision` in family archive
- Vision evolution → updates to `type: vision` record
- People entity updates → `type: person` records grow

### What L2 keeps that L1 never sees
- MLS group state, key rotation timestamps
- Concierge classifier confidence scores
- Wake-or-not decisions for messages that don't merit agent involvement

## What this pattern does NOT solve

- **Real-time queries on L1.** Live agent queries should hit L3 (Graphiti), which is derived from L1+L2. Querying L1 markdown directly works for ripgrep-scale searches but not sub-second graph traversal.
- **Cross-consumer L2 sharing.** Each consumer owns its L2. Sharing L2 across consumers (e.g., one Slack ingestion shared by Luce + TWIN) is out of scope for this pattern; would need a separate shared-ingestion service.
- **Replay from L2 alone.** L2 is operational; rotating it is fine. Replay-from-L2 is a debugging tool, not an institutional invariant. The institutional invariant is "L1 holds everything that needs to survive."

## Cross-references

- L1 substrate: [`l1/README.md`](../../l1/README.md)
- L1 frontmatter spec: [`l1/frontmatter-spec.md`](../../l1/frontmatter-spec.md)
- Schema vocabulary: [`schema/v1/`](../../schema/v1/)
- Decision-trace pattern: [decision-trace.md](decision-trace.md)
- Lifecycle vocabulary: [lifecycle-vocabulary.md](lifecycle-vocabulary.md)
- Original Path C documentation: TWIN-V1.md §3.11 (will be slim-referenced once this lives upstream)
