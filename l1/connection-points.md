# L1 Connection Points

> Source: omniscia/twin TWIN-V1.md §3.7.

The same canonical corpus is accessible via multiple windows. Each gives full context (cross-references resolve through every protocol) by virtue of using the same content-addressable IDs.

| # | Connection point | Use case | Auth |
|---|---|---|---|
| 1 | **Git clone** | Power-user access; offline capability; full corpus version history | SSH key or HTTPS-with-token from vault |
| 2 | **Static web UI** (`memory.<consumer-domain>`) | Browse + search + share links. Works on phone. PWA-installable | Public-tier no-auth; private-tier passkey + 2FA |
| 3 | **Plain HTTPS raw markdown serving** | No app needed; opens in any browser | Same as #2 |
| 4 | **HTTP API (search + get-by-id)** | Programmatic access for tools and agents | API token from vault |
| 5 | **IPFS gateway with permanent CIDs** | Decentralized fallback when any single host is down | None for public; cipher for private (encrypted bodies) |
| 6 | **Email digest** (daily/weekly) | Pull-mode legibility for non-technical readers | Email auth |
| 7 | **Printed PDF snapshots** (quarterly) | Paper backup. Survives all digital infrastructure | Physical possession |

## Permalink convention

Every memory has a stable permalink:

```
<host>/m/<ulid>
```

The host is your domain (Cloudflare or Porkbun registrar, transfer-locked, multi-year prepaid). Resolver points at whichever host is up.

## Why multiple connection points

The substrate must be **survivable across tool obsolescence**. Any single connection point can disappear without breaking the others — the corpus and IDs are universal. A 2050 reader with only HTTPS and a markdown viewer can still navigate.

## Per-consumer adaptation

Each Tabula consumer picks which connection points to expose. Bower might only expose #1 (git) and #4 (API) for the family agent. TWIN exposes all 7. Luce exposes #1, #2, #4 for production-tracking UIs. The substrate doesn't care.
