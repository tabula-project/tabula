# Path C: Working Memory + Long-Term Memory Pattern

> Source: omniscia/twin TWIN-V1.md §3.11. The canonical Tabula ETL adapter pattern.
>
> **See also:** [`docs/patterns/dual-tier-memory.md`](../docs/patterns/dual-tier-memory.md) — the cross-layer pattern catalog entry. This file is the L2↔L1 implementation guide; the patterns/ entry is the design-principle write-up.

The Path C pattern handles applications that produce a high-volume operational stream (chat messages, API calls, MCP tool invocations) that needs both fast operational access AND durable long-term memory.

## The two-tier model

Mirrors the working-memory / long-term-memory split in human cognition.

| Tier | Owner | Storage | Purpose |
|---|---|---|---|
| **Working memory** | Per-app plugin | SQLite (or other operational store) | Tactical state: session context, recent message buffer, dedupe cache, scrubbing pipeline, conversational history |
| **Long-term memory** | Tabula L1 | Markdown + frontmatter in audience-tiered git archives | Durable record: every meaningful event also lands as an L1 markdown file, audience-tagged, ULID-identified, queryable across the substrate |

## Dual-write semantics

Every event that flows through the application path is **dual-written**:

1. The SQLite (or equivalent) layer captures it for operational needs — fast read, mutable, can be rotated/wiped.
2. A Tabula-formatted markdown file is committed to the appropriate archive's `.tabula/` (or named) folder for long-term retention.

The SQLite layer can be wiped or rotated without losing memory. **The substrate is the canonical record.**

## Audience mapping per event

Each event source has a mapping rule:

```yaml
# Example mapping table (consumer-defined)
audience_map:
  "telegram://family-coord":  family
  "telegram://botho":          inner-circle
  "telegram://omniscia_bot":  self-only
  "kitsu://eve-production":  org-goodstudios
  default:                  self-only  # safe fallback; surfaces for human review
```

Unmapped sources default to the most-private tier and surface for human classification on next read.

## Worked example: gt-messaging (Telegram + Signal + WhatsApp)

The `gt-messaging` plugin (in `omniscia/twin/messaging/`) is a reference implementation:

- **Working memory:** SQLite at `<runtime>/messaging/messages.db` and `conversations.db`
- **Long-term memory:** Markdown commits to `omniscia/family/`, `omniscia/self/`, etc., depending on conversation classification
- **Adapter:** Telethon for Telegram, signal-cli for Signal, whatsmeow for WhatsApp — all unify to a normalized `Message` shape before dual-write
- **Scrubber:** PII/phantom scrubbing pipeline runs before commit (prevents accidental exposure of, e.g., one-time codes in long-term memory)

This implementation predates the substrate but its dual-write architecture is the template.

## Why this matters

Without Path C, applications either:
- **Keep everything in SQLite** — fast, but unsearchable across consumers, lost when SQLite rotates
- **Write everything directly to L1** — durable, but every chat message becomes a git commit (signal-to-noise collapse)

Path C lets working memory be operationally cheap (SQLite, mutable, fast) while *meaningful* events graduate to L1 (durable, typed, cross-substrate queryable). The application's distillation rules decide what counts as meaningful.

## When to use Path C vs. direct L1 capture

| Situation | Pattern |
|---|---|
| Single human capture event (note, decision, observation) | Direct L1 capture |
| High-volume operational stream (chat, API logs, telemetry) | Path C dual-tier |
| Bulk import (Signal history, Slack export, Kitsu backfill) | One-time ETL job → L1 |
| Real-time agent conversation | Path C; the conversation transcript graduates as a `conversation` L1 entry |
