# Model Routing Policy

> Last updated: 2026-05-04 (after enabling ChatGPT Codex + GitHub Copilot subscriptions)
> Audience: This rig's overseer + any agent picking a model for a task.

The point of running a multi-provider stack is to **route by task fit, not by
default model**. This document is the explicit policy. When in doubt, follow it.

## TL;DR — One-line rules

| Task | First choice | Why |
|---|---|---|
| Hard agentic coding | **Claude Opus 4.7** (Anthropic Max via pi-oauth) | SWE-bench leader (82.0%); on subscription |
| Backup for above (when Anthropic rate-limited) | **Claude Opus 4.7** (via github-copilot provider) | Redundant subscription path |
| Long-context analysis (>200K) | **Claude Opus 4.6** (1M ctx via copilot) or **Sonnet 4.6** (1M) | Both on Copilot subscription |
| Fast frontier general | **GPT-5.5** or **GPT-5.4** (openai-codex or copilot) | On subscription, big context (272K direct, 400K via copilot) |
| Codex-specialized coding | **gpt-5.4-codex / 5.3-codex** (openai-codex) | 400K context, codex-tuned, on subscription |
| Gemini for specific use cases | **gemini-3.1-pro-preview** (copilot) | On Copilot subscription, no API key needed |
| Bulk OSS coding (non-subscription work) | **Kimi K2.6** (Fireworks) | $0.95/$4.00, frontier-band quality |
| High-volume cheap | **DeepSeek V4 Pro** (Fireworks) | $1.74/$3.48, 1M ctx |
| Fast interactive | **Qwen 3 235B Instruct** (Cerebras Free) | ~2000 tok/s, MoE |
| Offline / private / free | **MLX Qwen3-Coder 30B-A3B** (local) | Zero cost, no network |
| Embeddings | **Ollama nomic-embed-text** (local) | Free |

## The subscription matrix

This is now the most important table. **Three subscriptions cover almost everything.**

| Provider | Subscription | What you get |
|---|---|---|
| **Anthropic Max** | $200/mo (existing) | Direct Opus 4.7 access via Pi + pi-oauth, primary path for hardest agentic work |
| **ChatGPT Plus/Pro** | ~$20-200/mo | Codex models directly (gpt-5.4-codex, 5.3-codex, 5.5) at 272K ctx, on subscription |
| **GitHub Copilot** | ~$10-20/mo (existing) | Almost everything else, on subscription: Anthropic Opus/Sonnet/Haiku 4.5+, OpenAI GPT-5.5/5.4/codex variants at 400K ctx, Gemini 3.1 Pro Preview, Grok Code Fast 1 |

**Killer insight**: Copilot's subscription terms include reselling the major
frontier vendors' models at flat rate. The same `claude-opus-4.7` that
costs $5/$25 per MTok at the API list price is on Copilot for a fraction of
that, accessed as a `github-copilot` provider model in Pi.

This means **the right-to-leave argument shifts**: instead of "we keep OSS as
fallback if proprietary deprecates," it's now "we have *three independent
subscription paths* to most frontier models, plus OSS, plus local." The fault
tolerance is much higher than it was.

## Verified live model catalog (2026-05-04)

After `pi /login` for both Codex + Copilot:

### `anthropic` provider (direct, via Pi OAuth + pi-oauth)
- claude-opus-4-7, 4-6 (1M ctx), 4-5, 4-1, 4-0
- claude-sonnet-4-6 (1M), 4-5, 4-0
- claude-haiku-4-5
- All routed through Anthropic Max via the pi-oauth message-shape fix

### `openai-codex` provider (ChatGPT Plus/Pro)
- gpt-5.5, 5.4, 5.4-mini
- gpt-5.4-codex, 5.3-codex, 5.3-codex-spark, 5.2-codex, 5.1-codex-max, 5.1-codex-mini
- gpt-5.2, 5.1
- All 272K context, on subscription

### `github-copilot` provider (GitHub Copilot subscription)
- **Anthropic**: claude-opus-4.7, 4.6 (1M), 4.5; sonnet-4.6 (1M), 4.5, 4; haiku-4.5
- **OpenAI**: gpt-5.5, 5.4, 5.4-mini, 5.3-codex, 5.2-codex, 5.2, 5.1-codex-max, 5.1-codex-mini, 5.1-codex, 5.1, 5, 5-mini, 4.1, 4o
- **Google**: gemini-3.1-pro-preview, 3-pro-preview, 3-flash-preview, 2.5-pro
- **xAI**: grok-code-fast-1
- All 128K-400K context, on subscription

### `fireworks` provider (per-token API key)
- kimi-k2p6 (256K), kimi-k2p5 (256K), kimi-k2-thinking (256K, reasoning)
- deepseek-v4-pro (1M ctx)
- glm-5p1 (reasoning), glm-5, glm-4p7, glm-4p5, glm-4p5-air
- minimax-m2p7, m2p5, m2p1
- gpt-oss-120b, gpt-oss-20b
- qwen3p6-plus
- Per-token billing, no subscription option

### `cerebras` provider (Free tier API key)
- qwen-3-235b-a22b-instruct-2507 (accessible)
- llama3.1-8b (accessible)
- zai-glm-4.7 (gated behind Cerebras Code Pro $50/mo, sold out)
- gpt-oss-120b (gated behind Cerebras Code, sold out)

### Local providers
- **mlx**: Llama-3.3-70B-4bit, Qwen3-Coder-30B-A3B-4bit
- **ollama**: qwen3-coder:30b, qwen2.5-coder:7b, qwen2.5:72b-instruct, llama3.3:70b-q4

## Tiers (in order of capability per task fit)

### Tier 0a — Frontier on subscription (preferred when on subscription)

These give the same models you'd pay $5+/MTok for via API, but for a flat
monthly fee. **This is the default tier for almost everything now.**

| Model | Best path | Subscription |
|---|---|---|
| Claude Opus 4.7 | `anthropic` (direct + pi-oauth) | Anthropic Max ✅ |
| Claude Opus 4.7 (backup) | `github-copilot` | GitHub Copilot ✅ |
| Claude Opus 4.6 (1M ctx) | `github-copilot` | GitHub Copilot ✅ |
| Claude Sonnet 4.6 (1M ctx) | `github-copilot` | GitHub Copilot ✅ |
| GPT-5.4 / 5.5 | `openai-codex` (direct) | ChatGPT Plus/Pro ✅ |
| GPT-5.4-codex (400K ctx) | `github-copilot` | GitHub Copilot ✅ |
| Gemini 3.1 Pro Preview | `github-copilot` | GitHub Copilot ✅ |
| Grok Code Fast 1 | `github-copilot` | GitHub Copilot ✅ |

**When to use which subscription path:**
- **Anthropic Max direct** for highest-priority Opus work — guaranteed quota, plus the pi-oauth message-shape fix
- **Codex direct** when using Codex-specialized variants for terminal/IDE workflows
- **Copilot** for everything else — Gemini, Grok, redundancy, mixing models in one session

### Tier 0b — Frontier per-token (rent only when subscription quota is exhausted)

Should rarely fire. Only if all three subscriptions are rate-limited or down
or you want a model that's not in the subscription catalogs.

- Anthropic Claude API direct: $5/$25 per MTok for Opus 4.7
- OpenAI API direct: list pricing
- Google Gemini API: $2/$12 per MTok

### Tier 1 — OSS frontier on Fireworks (sovereign-aligned)

The right-to-leave hedge. Use when:
- Sovereignty matters (audit trail, vendor independence)
- Cost matters more than top quality
- You want to test/compare against OSS frontier
- You hit subscription rate limits

| Model | Cost ($/M in / out / cached) | Use when |
|---|---|---|
| Kimi K2.6 (1T MoE) | 0.95 / 4.00 / 0.16 | Default OSS pick |
| DeepSeek V4 Pro | 1.74 / 3.48 / 0.145 | 1M context cheaper than Opus per-token |
| Kimi K2.5 | 0.60 / 3.00 / 0.10 | Cheaper fallback |
| Kimi K2 Thinking | (reasoning, see Fireworks) | Reasoning OSS at speed |
| GLM 5.1 (reasoning) | 1.40 / 4.40 / 0.26 | Explicit reasoning tokens |

### Tier 2 — Cerebras speed lane (Free tier)

Free, ~2000 tok/s, but limited to non-coder models without Cerebras Code sub.

| Model | Use when |
|---|---|
| Qwen 3 235B Instruct | Fast general tasks (chat, summaries, classification) |
| Llama 3.1 8B | Trivial classification only |

### Tier 3 — Local sovereign (Mac Studio M3 Ultra, 96 GB)

Free, private, offline. Capped at ~70B class on this hardware.

| Model | Engine | Strengths |
|---|---|---|
| Qwen3-Coder 30B-A3B 4-bit | MLX | Primary local coder (256K ctx) |
| Llama 3.3 70B 4-bit | MLX | General reasoning |
| qwen3-coder:30b | Ollama | Same model, Ollama serving |
| qwen2.5-coder:7b | Ollama | Fast inline completion |
| nomic-embed-text | Ollama | Embeddings |

## Routing decision tree (post-Copilot)

```
Is the task latency-critical (interactive REPL)?
├── YES → Cerebras Qwen 3 235B Instruct (free, fast)
└── NO ↓

Is data sensitive / must stay local?
├── YES → MLX Qwen3-Coder 30B-A3B
└── NO ↓

Is this hard agentic coding (multi-step, multi-file)?
├── YES → anthropic/claude-opus-4-7 (Max sub via pi-oauth)
│         backup: github-copilot/claude-opus-4.7 (Copilot sub)
└── NO ↓

Need 1M+ context?
├── YES (best quality)  → github-copilot/claude-opus-4.6 (1M, sub)
├── YES (cheaper)       → fireworks/deepseek-v4-pro (1M, $1.74/$3.48)
└── NO ↓

Codex-specialized task (terminal, agentic loop)?
├── YES → openai-codex/gpt-5.4-codex or github-copilot/gpt-5.4-codex
└── NO ↓

Want Gemini for specific use case?
├── YES → github-copilot/gemini-3.1-pro-preview (on sub, no API key needed)
└── NO ↓

Default: anthropic/claude-opus-4-7 (best subscription path).
Fall back to OSS only if cost/sovereignty/redundancy specifically demands it.
```

## Cost reference

For tasks where you want frontier quality, **all of these are now on
subscription**:

| Model | Path | Effective cost |
|---|---|---|
| Claude Opus 4.7 | anthropic + pi-oauth | Max plan ($200/mo cap) |
| Claude Opus 4.7 / 4.6 / Sonnet 4.6 | github-copilot | Copilot plan |
| GPT-5.5 / 5.4 / codex variants | openai-codex | ChatGPT plan |
| GPT-5.4 / Gemini 3.1 / Grok | github-copilot | Copilot plan |

For tasks that don't need frontier quality, **OSS is dramatically cheaper**:

| Model | $ in | $ out | $ cached-in | vs Opus list |
|---|---|---|---|---|
| Claude Opus 4.7 (list) | 5.00 | 25.00 | — | 1× baseline |
| GPT-5.4 (list) | 2.50 | 15.00 | — | 1.7× cheaper |
| Gemini 3.1 Pro (list) | 2.00 | 12.00 | — | 2.1× cheaper |
| Kimi K2.6 (Fireworks) | 0.95 | 4.00 | 0.16 | 6.3× cheaper |
| DeepSeek V4 Pro (Fireworks) | 1.74 | 3.48 | 0.145 | 7.2× cheaper |
| Kimi K2.5 (Fireworks) | 0.60 | 3.00 | 0.10 | 8.3× cheaper |
| Local MLX/Ollama | 0 | 0 | — | electricity only |

## Subscription routing — how each provider gets there

### Anthropic via Pi + `pi-oauth` (primary path)

[`tmustier/pi-oauth`](https://github.com/tmustier/pi-oauth) reshapes Pi's
Anthropic OAuth requests so `system[]` matches the actual format Claude Code
sends — avoiding the post-2026-04-04 Extra Usage misclassification.

Mechanism: Pi's harness prompt moves from `system[]` to a synthetic first
`user` message wrapped in `<system-reminder>...</system-reminder>` blocks.

Status: ✅ INSTALLED at `~/pi-oauth/`, registered in `~/.pi/agent/settings.json`.

### OpenAI Codex via `pi /login`

`pi /login` → ChatGPT Plus/Pro/Team OAuth flow → stored in `~/.pi/agent/auth.json`.
[Officially endorsed by OpenAI](https://developers.openai.com/community/codex-for-oss).

Status: ✅ LOGGED IN.

### GitHub Copilot via `pi /login`

`pi /login` → GitHub OAuth (or GitHub Enterprise Server domain) → stored in
auth.json. Models accessed need to be enabled in your Copilot configuration.

If a specific model returns "model not supported", enable it in VS Code:
Copilot Chat → model selector → select model → "Enable".

Status: ✅ LOGGED IN.

### Fireworks API key

Per-token billing. Stored in auth.json as `{"type": "api_key", "key": "fw_..."}`.

Status: ✅ INSTALLED, key rotated post-paste.

### Cerebras API key (Free tier)

Per-token billing on Free tier (rate-limited). For coder models, would need
Cerebras Code Pro/Max ($50/$200/mo, both currently sold out).

Status: ✅ INSTALLED, key rotated post-paste.

## Loom: removed (2026-05-04)

`~/.loom/` was a multi-account Claude OAuth token pool. With the subscription
stack now covering frontier access via three independent providers, Loom is
even less relevant — pool rotation is for spreading quota across accounts of
the same provider, but our redundancy now comes from three separate providers.

Three pieces of useful infrastructure that lived under `~/.loom/` were
relocated, not deleted:

1. **Pi crash logger** → `~/.local/lib/pi-crash-logger.js`
2. **R2 backup system** → `~/.local/share/r2-backup/`
3. **Per-project handoff state** → `~/.local/share/handoff/`

See `scripts/migrate-from-loom.sh` for the portable migration recipe.

## Sovereignty principle

> The right to **leave** is the property we are protecting, not the act of leaving.

We now have **three independent subscription paths** to the proprietary
frontier (Anthropic Max, ChatGPT Plus/Pro, GitHub Copilot), plus **two OSS
tiers** (Fireworks per-token + local MLX/Ollama). Five paths total. The
chance of all five disappearing simultaneously is effectively zero. Build
workflows that *can* function on any one of them; default to the cheapest
viable for each task.

## OAuth subscription — overall action items (post-Codex/Copilot)

1. ~~`pi-oauth`~~ ✅ INSTALLED
2. ~~OpenAI Codex login~~ ✅ DONE
3. ~~GitHub Copilot login~~ ✅ DONE — and unlocked Gemini + Grok in the bargain
4. ~~Fireworks key~~ ✅ INSTALLED + ROTATED
5. ~~Cerebras key~~ ✅ INSTALLED + ROTATED
6. **Cerebras Code Max** — watch for stock to return ($200/mo unlocks GLM 4.7 + gpt-oss-120b at speed)
7. ~~Gemini API key~~ ❌ NOT NEEDED — covered by Copilot subscription
8. **First real workload test** — pick a task you'd normally use Opus for, route via Kimi K2.6 (Fireworks), document the gap. Validates the OSS fallback at workload level, not just auth-probe level.
