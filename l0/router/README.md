# Tabula L0 Router

> **Vendored from `omniscia/pi-router` on 2026-05-07.** The canonical implementation now lives in this repo at [`src/`](src/). Future development happens here; `omniscia/pi-router` is preserved as a redirect.
>
> Package: `@tabula/router` (was `pi-router`).
>
> History: pi-router was the v0 implementation built before Tabula consolidated the substrate. Now that Tabula owns L0, the router code lives with the rest of L0.

---

**Local-first adaptive model router.** OSS-first routing with offline support, feedback-driven learning, and 100% local decision-making.

> Status: v0 — heuristic rules + feedback capture + stats. v0.5 (embedding-based learning) and v1 (local LLM tiebreaker) coming next.

## What it does

For every prompt you send through Pi, this extension:

1. Extracts features (prompt length, code-fence presence, keywords like "concurrency"/"refactor"/"debug", file references, working-directory rig, network state, etc.)
2. Runs Layer 1 rules — pure code, no model — to pick the best-fit model from your catalog
3. Calls `pi.setModel()` to swap to the chosen model before the LLM call goes out
4. Shows you the decision inline: `🔀 routed to fireworks/kimi-k2p6 · layer:rules rule:default · reason: default → Kimi K2.6 (OSS-first)`
5. Records the decision to a SQLite store (`~/.local/share/pi-router/decisions.db`)
6. After the response, you press `Shift+Ctrl+G` (good) or `Shift+Ctrl+B` (bad). Optionally `/bad missed the race condition` for a free-text reason. (Plain `Ctrl+G` and `Ctrl+B` are reserved by Pi for the external editor and cursor-left.)
7. Stats accumulate; `/router-stats` shows a dashboard

The router itself runs **entirely on your machine**. No cloud router. No API call to decide which model to use. No data leaves your laptop unless you're routing to a cloud model — and that decision was made locally.

## OSS-first defaults

Default model: **Claude Opus 4.7** (subscription-routed; reliable). OSS-first philosophy preserved via specific routing rules — the Opus default is the fallback when no specific rule fires. Earlier versions defaulted to Kimi K2.6 on Fireworks but Fireworks rate-limit hits were crashing pi sessions; the default was flipped on 2026-05-07.

Escalation rules:

| Signal | Routes to |
|---|---|
| Concurrency / race-condition mentions | Claude Opus 4.7 |
| Context > 200K tokens | DeepSeek V4 Pro (1M ctx, OSS) |
| Formal reasoning keywords (prove, theorem, big-O) | GLM 5.1 (reasoning OSS) |
| Debug + large context | Kimi K2 Thinking (reasoning OSS) |
| Explain code | Kimi K2.5 (OSS, cheaper) |
| Multi-file refactor | DeepSeek V4 Pro |
| **Offline** + code task | MLX Qwen3-Coder 30B-A3B (local) |
| **Offline** + general | MLX Llama 3.3 70B (local) |
| Default | Claude Opus 4.7 |

Note: Cerebras Free auto-routing was removed 2026-05-07 (rate limits unreliable). Users can still pick Cerebras manually via Ctrl+P.

Edit `src/rules.ts` to tune.

## Install

Clone, install deps, and register with Pi:

```bash
cd ~
git clone https://github.com/omniscia/pi-router.git
cd ~/pi-router
npm install
pi install ~/pi-router
```

Or via Pi's package syntax (after cloning):

```bash
pi install ~/pi-router        # global
pi install ~/pi-router -l     # project-local
```

## Manual override

Press `Ctrl+P` *before* sending a message to cycle through your `models` list (configured in `~/.pi/agent/settings.json`). The router detects that you manually selected a model and records the turn as a `manual-override` — your choice is respected, and the override is logged as a signal for future rule tuning (frequent overrides on a particular feature pattern → suggest a rule).

## Slash commands

| Command | What |
|---|---|
| `/good [reason]` | Mark last decision as good (or use Shift+Ctrl+G) |
| `/bad [reason]` | Mark last decision as bad (or use Shift+Ctrl+B) |
| `/router-stats [days]` | Aggregate dashboard (default 30 days) |
| `/router-explain [turnId]` | Show why a past decision was made (latest if omitted) |
| `/router-verbose <level>` | Set inline verbosity: `debug` / `always` / `escalations` / `quiet` / `silent` |
| `/router-enable` / `/router-disable` | Toggle routing for the session |
| `/router-where` | Show paths to the SQLite store and config |

## Verbosity levels

| Level | Status bar | Inline pre-response | Features+alternatives shown |
|---|---|---|---|
| `debug` (install default) | ✅ | ✅ on every turn | ✅ full |
| `always` | ✅ | ✅ on every turn | brief |
| `escalations` | ✅ | only when not Kimi K2.6 | brief |
| `quiet` | ✅ | never | — |
| `silent` | — | — | — |

## What's stored locally

- **`~/.local/share/pi-router/decisions.db`** — SQLite. Per-turn: chosen model, layer, rule, features (JSON), feedback, cost. WAL journal for safety.
- **`~/.local/share/pi-router/config.json`** — Verbosity setting + future router config.

Nothing leaves the machine. Inspect anytime with sqlite3.

## Architecture

```
Pi receives a prompt
  ↓
before_agent_start hook fires (this extension)
  ↓
[Layer 1] features → rules → chosen model
  ↓
pi.setModel(chosen) — swaps the model before the LLM call
  ↓
Inline 🔀 message displayed (if verbosity allows)
  ↓
Pi sends request, response streams back
  ↓
You press Shift+Ctrl+G / Shift+Ctrl+B (or /good /bad)
  ↓
Feedback applied to decision row in SQLite
```

## Roadmap

- **v0.5**: Layer 2 — embedding nearest-neighbor lookup using `nomic-embed-text` (local Ollama). For ambiguous cases where rules don't fire decisively, embed the prompt, find K nearest historical prompts in SQLite, weighted vote on chosen model. Genuinely learns from your feedback.

- **v1.0**: Layer 3 — local LLM tiebreaker via `qwen2.5-coder:7b` (already in your Ollama). Used only when rules + embedding lookup disagree. Sub-1s decision time. Still 100% local.

- **v1.1**: Suggested rule tweaks. When stats reveal a rule has <50% good-rate or a feature pattern consistently gets manual-overridden, suggest a new rule.

- **v2.0**: Cross-rig stats. See routing patterns per project.

## Development

```bash
cd ~/pi-router
npm install
npm run typecheck
```

Hot reload in a Pi session: `/reload`.

## Why this exists

Most "model routers" call out to a cloud LLM (often Claude or GPT) to decide which other LLM to use. That's:

1. A single point of failure (no internet → no router → no agent)
2. A privacy leak (every prompt goes to the routing vendor)
3. Marginal cost ($0.005-0.02 per query just for routing decisions)
4. Architectural absurdity (paying Anthropic to decide whether to use Anthropic)

This router does the decision in pure code. Pi already runs locally; the routing should too. The only reason a cloud model gets called is if the rules pick one — and you can audit every rule.

## License

MIT. See `LICENSE`.
