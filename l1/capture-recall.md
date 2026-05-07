# L1 Capture and Recall API

> Source: omniscia/twin TWIN-V1.md §4.1, §4.2.

The substrate's daily UX surface. Tabula ships reference implementations; consumers wrap or replace them.

## Capture (write to L1)

Goal: <5 seconds from intent to commit.

| Surface | Path |
|---|---|
| **CLI** | `tabula capture <archive> <audience>` → opens `$EDITOR`, writes file, commits, pushes |
| **Phone shortcut** | iOS Shortcut → `POST /api/capture` → corpus entry → graph re-index → push notification |
| **Bot** | Telegram/Signal message to consumer's bot → corpus entry |
| **Voice memo** | Whisper.cpp transcription → corpus entry with `source: voice://...` |
| **Email** | `capture@<consumer-domain>` → MIME parsed → corpus entry |

All capture paths produce a markdown file with valid frontmatter, commit it, and push to the configured remotes.

## Recall (read from L1 + L3)

Goal: <2 seconds from query to answer.

| Surface | Path |
|---|---|
| **Bot chat** | "what did rjwalters say about lottery progressivity?" → graph query → answer with citation |
| **Web UI** | Same query, prettier UI, deep-linkable to the cited memory |
| **CLI** | `tabula recall "<query>"` — ripgrep over local clone for offline; graph for online |
| **MCP tool-call** | Agents query via Graphiti's MCP interface |

## Deep reflection (longer session)

Multi-turn conversation with full corpus context. Runs on the consumer's chosen frontend (OpenWebUI, Anthropic Console, custom). Tabula provides:
- Audience-aware retrieval (only memories the requesting tier can read)
- Citation-grounded responses (every claim links to a memory ULID)
- Multi-document RAG over the graph

## Multi-machine bootstrap

Time to a working substrate on a new machine:

| Setup level | Time | What works |
|---|---|---|
| **Minimum usable** | ~10 min | CLI + bot + chat surface — capture and recall |
| **Daily UX** | ~30 min | + password vault, fully signed in |
| **Full power-user** | ~60 min | + corpus clones, all CLI tools, local graph rebuild |

The vault is the master key. Once the password manager is unlocked (master password + passkey from another device or recovery code), everything else cascades. Bootstrap doc lives in the vault.

## Mobile parity

The substrate is fully usable from a phone:
- Bot chat for capture and recall
- Static web UI for browsing
- PWA install for offline browse of recent memories
- Email digest for pull-mode legibility

No critical operation requires a laptop.
