# L1 Replication and Archival

> Source: omniscia/twin TWIN-V1.md §3.8.

## Two-track principle

**Corpus for durability, graph for speed.**

The corpus (L1 markdown + git) is the source of truth and replicated for survival. The graph (L3) is rebuilt from the corpus on any host as needed — never archived, never the canonical record.

## Git replicas (≥3 jurisdictions recommended)

| Replica | Jurisdiction | Operator | Purpose |
|---|---|---|---|
| **GitHub** (`<consumer-org>/*`) | US | Microsoft | Primary for public/org-tier |
| **Codeberg** | Germany | Non-profit | Secondary, EU jurisdiction |
| **Self-hosted Forgejo** on Hetzner | Germany or France | Yours | Tertiary; only home for most-private tiers (`spouse-only`, `self-only`) |

Each consumer picks at least three jurisdictions for non-correlated failure. The substrate is encrypted per audience tier (see [`encryption.md`](encryption.md)) so private tiers can safely live on multiple hosts.

## Industrial archival

| Archive | Use | Cost | Frequency |
|---|---|---|---|
| **Arweave** (permanent decentralized storage) | All archives, public + private (encrypted) | $5–20 per snapshot | Monthly |
| **Zenodo** (CERN-backed, indefinite, gives DOIs) | Public-tier substrate snapshots | Free | Quarterly |
| **AWS Glacier Deep Archive** | Cold backup of binaries (photos, videos) | $0.99/TB/month | Yearly |
| **Encrypted USB drive** in safe deposit box | Offline tier | Negligible | Quarterly rotation |

## Recovery hierarchy

Designed for graceful degradation: every disaster has a recovery path that doesn't require the previous tier.

| Speed | Method |
|---|---|
| **Sub-second** | Graph query via Postgres+AGE+pgvector |
| **Seconds** | `ripgrep` over local corpus clone |
| **Minutes** | Clone fresh from any remote + rebuild graph |
| **Hours** | Pull from Arweave or Glacier |
| **Days** | Shamir reconstruction + sealed-envelope retrieval (if private tier) |

## Why this matters

The substrate's durability claim is load-bearing. A consumer that puts everything on a single GitHub repo with no offline copy is using the markdown+git pattern but not the Tabula substrate guarantee. The replication strategy is part of the spec, not optional polish.
