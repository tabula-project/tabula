# Five-Layer Substrate Architecture

> Source: omniscia/twin architecture/TWIN-V1.md §3.1a. Ratified in hq-2kxn. Tabula is the canonical home.

The substrate is structured as five layers, three of which are shared across all consumers:

```
┌──────────────────────────────────────────────────────────────┐
│  LAYER 4 — APPLICATION (per-app, distinct logic)             │
│  Luce: doublets / conservation / commitment gate / L2         │
│    coordinator / studio brain                                │
│  TWIN-personal: capture/recall CLI + bot adapters           │
│  Bower: MLS chat surface + concierge + family agent          │
├──────────────────────────────────────────────────────────────┤
│  LAYER 3 — KNOWLEDGE GRAPH (shared) (Graphiti)              │
│  Live query, derived from L1 + L2, sub-second response    │
├──────────────────────────────────────────────────────────────┤
│  LAYER 2 — OPERATIONAL LOG (per-app, sized to need)         │
│  Append-only structured events, distillable to L1            │
│  Luce: heavy (every Kitsu/Slack/MCP call captured)           │
│  TWIN: medium (Signal/Telegram/email/calendar/voice)         │
│  Bower: light (family chat + calendar)                     │
├──────────────────────────────────────────────────────────────┤
│  LAYER 1 — SUBSTRATE (shared) (markdown + git, decadal)    │
│  Authoritative entities, decisions, observations             │
├──────────────────────────────────────────────────────────────┤
│  LAYER 0 — SOVEREIGN COMPUTE (shared)                      │
│  OSS weights on owned infra; cloud-OSS fallback;           │
│  cloud-frontier last resort. Privacy-class routing.          │
└──────────────────────────────────────────────────────────────┘
```

## Shared infrastructure

- **L0** — Sovereign compute: model registry, privacy-class router, sleep API
- **L1** — Substrate: markdown + git, frontmatter spec, schema vocabulary
- **L3** — Knowledge graph: Graphiti adapter over Postgres + Apache AGE + pgvector
- **Schema vocabulary** — canonical content types (`vision`, `person`, `place`, `event`, `project`, `tool`, `decision`, `observation`, `conversation`)
- **ETL adapter framework** — L2 → L1 distillation pipelines

## Per-application

- **L2** — Operational log, sized to ingestion volume. Each consumer writes its own.
- **L4** — Application logic. Deliberately divergent across consumers.

## Governance principle

No application logic (L4) constrains or reaches into the substrate (L0/L1/L3). Luce's Quantum Architecture (doublets, conservation laws, commitment gate, E=mc² metric) is application logic on top of L1+L2+L3 — it doesn't constrain or reach into the substrate. TWIN-personal's capture/recall CLI is much simpler and lives at the same level. Bower's MLS-encrypted chat with concierge classifier is yet another L4. None of these care which other applications exist.
