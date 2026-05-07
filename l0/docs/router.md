# pi-router — adaptive model router

> Status: v0 SHIPPED 2026-05-04 — Layer 1 heuristic rules, feedback capture, stats. v0.5 (embedding learning) and v1 (local LLM tiebreaker) planned.

## What it is

A Pi extension that, for every prompt, runs heuristic rules to pick the
best-fit model from the catalog and swaps `pi.setModel()` before the LLM call
goes out. 100% local decision-making — no cloud router, no SPoF, works offline.

**Repo**: [omniscia/pi-router](https://github.com/omniscia/pi-router) (public, MIT)
**Local clone**: `~/pi-router/`
**Registered in Pi**: `~/.pi/agent/settings.json` packages array

## Locked design (per overseer review 2026-05-04)

| # | Decision | Choice |
|---|---|---|
| 1 | Manual override | Pre-send (Ctrl+P before submit). Router records as `manual-override`, doesn't change model |
| 2 | Feedback granularity | Binary (Ctrl+G/B) + optional `/bad <reason>` for free text |
| 3 | Prompt features | Full payload: prompt + ctx-tokens + system-prompt-hash + tools + cwd-rig + network state |
| 4 | Inline visibility | Configurable; install default `debug` (full transparency) |
| 5 | Code home | Public repo `omniscia/pi-router` (OSS, no secrets) |

## Architecture

Three layers — only Layer 1 implemented in v0; 2 and 3 stubbed for next phases.

```
Pi receives a prompt
  ↓
before_agent_start hook fires
  ↓
[Layer 1: rules]   features → ordered rules → first match wins
  ├── rule fires high-confidence → pick that model. STOP.
  └── no high-confidence rule → fall through ↓
  
[Layer 2: embeddings]    (v0.5) embed prompt with nomic-embed-text → KNN over historical decisions → vote
  ├── consensus → pick. STOP.
  └── no consensus → ↓
  
[Layer 3: local LLM tiebreaker]  (v1) qwen2.5-coder:7b decides
  ↓
pi.setModel(chosen)
  ↓
Inline 🔀 message displayed (verbosity-gated)
  ↓
Pi sends request, response streams back
  ↓
You press Ctrl+G/B → feedback in SQLite
```

## Rules in v0 (priority order)

| Priority | Rule | Triggers when | Routes to |
|---|---|---|---|
| 0 | `offline-code` | offline + code-task | MLX Qwen3-Coder 30B-A3B |
| 1 | `offline-default` | offline (any other) | MLX Llama 3.3 70B |
| 10 | `concurrency-bug` | mentions race/deadlock/concurrency | **Claude Opus 4.7** (escalation) |
| 20 | `huge-context-oss` | conv > 200K tokens | DeepSeek V4 Pro (1M ctx, OSS) |
| 30 | `reasoning-heavy` | mentions prove/theorem/big-O/invariant | GLM 5.1 (OSS reasoning) |
| 35 | `deep-debug` | debug + ctx > 50K | Kimi K2 Thinking |
| 40 | `quick-question` | < 250 tok, no code, no debug | Cerebras Qwen 3 235B (free) |
| 50 | `code-explain` | explain/summarize + code | Kimi K2.5 (cheaper) |
| 55 | `multi-file-refactor` | ≥ 5 file refs OR refactor keyword | DeepSeek V4 Pro |
| 1000 | `default` | none of the above | **Kimi K2.6** (OSS-first default) |

OSS-first by construction: only `concurrency-bug` escalates to a commercial model.

## Verified live (2026-05-04 17:54 UTC)

Smoke test: `cd /tmp && pi -p "Reply with exactly: pong"`

Result:
- Router fired `quick-question` rule (prompt was 28 chars, no code fences, no debug)
- Selected `cerebras/qwen-3-235b-a22b-instruct-2507`
- Cerebras Free responded "pong"
- Decision logged in `~/.local/share/pi-router/decisions.db`

Without the router this would have used the default (Anthropic Opus 4.7) at $0.001+ per turn. With router: $0.

## Storage

| Path | Contents |
|---|---|
| `~/.local/share/pi-router/decisions.db` | SQLite, every routing decision + features + feedback |
| `~/.local/share/pi-router/config.json` | Verbosity setting (created on first `/router-verbose`) |

## Operating commands

| Command | Purpose |
|---|---|
| `Ctrl+G` | Mark last decision as good |
| `Ctrl+B` | Mark last decision as bad |
| `/good [reason]` / `/bad [reason]` | Same with optional free-text reason |
| `/router-stats [days]` | Aggregate dashboard |
| `/router-explain [turnId]` | Why was a past decision made? |
| `/router-verbose <level>` | Set inline verbosity (`debug` / `always` / `escalations` / `quiet` / `silent`) |
| `/router-enable` / `/router-disable` | Toggle for current session |
| `/router-where` | Show storage paths |

## Integration with the sovereign-AI plan

This makes the OSS-first plan from `model-routing.md` automatic:
- Default daily-driver model is now Kimi K2.6 (Fireworks, OSS) — set per turn by the router
- Escalation to Opus 4.7 only on concurrency-bug detection
- Free Cerebras for all small standalone questions
- Local MLX when offline
- All decisions logged for assessment

The static routing policy in `model-routing.md` is now also operationally enforced, not just documented.

## What's next

- **v0.5 (1-2 weeks)**: Embedding-based Layer 2. Use `nomic-embed-text` (already installed in Ollama) to embed every prompt; on cache miss in rules, find K nearest historical prompts in SQLite, weighted vote on chosen model. **This is where genuine learning kicks in.**
- **v1.0 (+1 week)**: LLM tiebreaker via `qwen2.5-coder:7b` (already in Ollama) for novel/ambiguous cases.
- **v1.1**: Suggested rule tweaks. When stats reveal a rule has consistent bad-feedback or is frequently overridden, surface a suggestion: *"Add rule X?"*

## Cost & failure modes

**Cost overhead per query**: ~5-10ms feature extraction + ~1ms rule application + ~2ms SQLite write = **negligible** vs LLM inference time (typically seconds).

**Disk**: SQLite grows linearly with usage; ~1-2KB per decision. After a year of heavy use, expect ~50-100 MB. Restic backup already covers it.

**Failure modes**:
- Rule misfires → manual Ctrl+P override + Ctrl+B feedback. Stats reveal the misfire over time.
- Network drops mid-session → next decision sees the failure, marks offline 30s, auto-routes to local.
- Pi setModel fails (no auth for chosen model) → router falls back to MLX local default.
- SQLite locks/corruption → router still routes; just stops logging until restored. WAL journal makes corruption rare.
- Router itself crashes/throws → Pi continues with whatever model was previously set. Worst case: model didn't get changed for that turn.

## Disable temporarily

In any session: `/router-disable`. Re-enable with `/router-enable`.

To uninstall entirely:
```bash
pi remove ~/pi-router
rm -rf ~/.local/share/pi-router
```

(Keeps `~/pi-router/` repo clone — delete that too if desired.)
