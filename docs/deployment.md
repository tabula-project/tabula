# Tabula Deployment Guide

> Source: omniscia/twin TWIN-V1.md §3.10 (OSS-first stack) + §4.2 (bootstrap timing).

## OSS-first principle

Every load-bearing component is open-source. Closed services that remain (domain registrar, banking, telephony) are not load-bearing — the substrate continues working if any of them is replaced.

## Reference stack

| Layer | OSS choice | License |
|---|---|---|
| **Compute (L0)** | Owned hardware (e.g., Mac Studio) for `family_or_self`; Hetzner / GCP / Lambda for cloud-OSS; self-hosted GPU for `project` | Various |
| **Git host (primary)** | **Forgejo** (Gitea fork) on Hetzner | MIT |
| **Git host (secondary)** | **Codeberg** (Forgejo-based, non-profit) | MIT |
| **Git host (tertiary)** | **GitHub** | Closed but standard |
| **Index database (L3)** | **PostgreSQL + Apache AGE + pgvector + TimescaleDB** | PostgreSQL License + Apache 2.0 |
| **Knowledge framework** | **Graphiti** | MIT |
| **Vault** | **Vaultwarden** (Bitwarden-protocol-compatible) | AGPLv3 |
| **Backup vault** | **KeePassXC** (file-based, synced via git) | GPLv3 |
| **Binary storage** | **MinIO** (S3-compatible) | AGPLv3 |
| **Static-site generator** | **Hugo** or **Quartz** | MIT |
| **Web server** | **Caddy** (auto-TLS) | Apache 2.0 |
| **Encryption** | **age** | BSD-3-Clause |
| **Bot frameworks** | **python-telegram-bot**, **signal-cli**, **slack-bolt** | LGPLv3 / GPLv3 / MIT |
| **Email server** | **Stalwart** or **Mailcow** | AGPLv3 / various |
| **Auth standard** | **FIDO2 / WebAuthn passkeys** (synced via Apple iCloud / Google account) | Open standard |
| **Analytics** | **DuckDB** + **DuckDB-WASM** | MIT |

## Closed services (not load-bearing)

- **Domain registrar** (Cloudflare or Porkbun) — necessary commercial service; transfer-locked + multi-year prepaid
- **Banking / payments** — necessary; org-owned where possible
- **Telephony / SIM** — necessary; minimize via passkey + TOTP

## Infrastructure provider

**Hetzner** (Germany) for compute. Replaceable with OVH (France), Linode, or self-hosted hardware. The provider is closed; the software running on it is open.

## Deployment shapes

### Solo / family

- 1× Mac Studio (or equivalent) for L0 owned-infra
- 1× Hetzner VM for L1 git replica + L3 Postgres
- GitHub + Codeberg as additional L1 replicas
- Vaultwarden self-hosted on the Hetzner VM

Cost: ~$30–60/month + one-time hardware.

### Small org / studio

- L0: Owned GPU server (or rented Lambda) + Cerebras/Fireworks for cloud-OSS fallback
- L1: GitHub Enterprise + Codeberg + Forgejo on Hetzner
- L3: Hetzner managed Postgres or self-hosted on a dedicated VM
- Vaultwarden + MinIO for binary storage

Cost: ~$200–500/month depending on GPU usage.

### Multi-org reference deployment

To be specified in Phase 2. Will be the shared deployment that TWIN, Luce, and Bower all run on simultaneously to validate the substrate-not-product framing.

## Bootstrap timing on a new machine

| Setup level | Time | What works |
|---|---|---|
| **Minimum usable** | ~10 min | Bot + chat surface for capture and recall |
| **Daily UX** | ~30 min | + Vault, Nextcloud, fully signed in |
| **Full power-user** | ~60 min | + corpus clones, all CLI tools, local graph rebuild |

The vault is the master key. Once the password manager is unlocked (master password + passkey from another device or recovery code), everything else cascades. Bootstrap doc lives in the vault.
