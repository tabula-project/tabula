# Sovereign AI Stack Inventory

> Last verified: 2026-05-04
> Hardware: Mac Studio M3 Ultra, 96 GB unified memory

This is the canonical inventory of the local OSS AI stack plus subscription
routing to proprietary frontier models. The originals listed under "Source of
truth" are the live files; copies in `configs/` and `scripts/` are snapshotted
into git so we can diff drift.

## 1. Pi (OSS coding agent harness)

- **Source of truth**: `~/.pi/agent/models.json`
- **Snapshot**: `configs/models.json`
- **Settings**: `~/.pi/agent/settings.json` → snapshot `configs/settings.json`
  - default model: `claude-opus-4-7` at `high` thinking
  - registered packages: `pi-oauth` (at `~/pi-oauth/`)
- **Auth file**: `~/.pi/agent/auth.json` (mode 0600) — has `anthropic` OAuth entry
- Custom providers below; built-in providers (anthropic, openai, google, etc.) accessed via auth.json or env vars.

## 2. Ollama (local, `:11434`)

| Model | Role | Status |
|-------|------|--------|
| `qwen3-coder:30b` | Primary coder | installed (18 GB) |
| `qwen2.5-coder:7b` | Fast coder | installed (4.7 GB) |
| `qwen2.5:72b-instruct` | General | installed (47 GB) |
| `llama3.3:70b-instruct-q4_K_M` | General | installed (42 GB) |
| `nomic-embed-text` | Embeddings | installed |

## 3. MLX-LM (local, `:8081`)

- **LaunchAgent**: `~/Library/LaunchAgents/com.user.mlx-lm.plist`
  → snapshot: `configs/com.user.mlx-lm.plist`
- **Helper scripts**: `~/.local/bin/mlx-server-run`, `~/.local/bin/mlx-switch`
  → snapshots: `scripts/mlx-server-run`, `scripts/mlx-switch`
- **Current-model file**: `~/.local/etc/mlx/current-model` → `mlx-community/Llama-3.3-70B-Instruct-4bit`
- **Logs**: `~/Library/Logs/mlx-lm.{out,err}.log`
- **Switch model**: `mlx-switch <hf-repo-id>` (reloads LaunchAgent)
- **Install**: `uv tool install mlx-lm` (NOT pip — PEP 668 system Python)

Models available on the server:
- `mlx-community/Llama-3.3-70B-Instruct-4bit`
- `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit`

## 4. Fireworks (cloud OSS weights)

- **Auth**: `FIREWORKS_API_KEY` env var (NOT YET SET)
- Models served:
  - **Kimi K2.6** — current OSS frontier, 256K ctx ($0.60 / $2.50)
  - **DeepSeek V3.2** — cheapest frontier-tier ($0.29 / $0.43)
  - DeepSeek V3.1 (prior gen, $0.56 / $1.68)
  - Qwen3-Coder 480B ($0.45 / $1.80)
  - Kimi K2 Instruct (prior gen, fallback)
  - GLM 4.6 (reasoning, $0.55 / $2.19)
- **TODO**: model IDs need verification against current Fireworks catalog when key is added. Specifically `kimi-k2-6-instruct` and `deepseek-v3p2` are educated guesses based on Fireworks naming conventions.

## 5. Cerebras (cloud OSS, ultra-fast)

- **Auth**: `CEREBRAS_API_KEY` env var (NOT YET SET)
- Models served:
  - Llama 3.3 70B (~2000 tok/s, $0.85 / $1.20)
  - Qwen3-Coder 480B ($2.00 / $2.00)
- **TODO**: same as Fireworks — verify model IDs at activation.

## 6. Anthropic (Claude Opus 4.7) — built-in to Pi, subscription routing

**Path**: Pi `auth.json` Anthropic OAuth + `pi-oauth` extension at
`~/pi-oauth/` (registered in `~/.pi/agent/settings.json` packages).

Pi serializes Anthropic OAuth requests Claude-Code-style. The `pi-oauth`
extension hooks `before_provider_request` and reshapes `system[]` so Pi's
harness prompt lands in a `<system-reminder>` user message instead of trailing
`system[]` blocks — matching the actual format Claude Code sends. This avoids
the post-2026-04-04 Extra Usage misclassification that Anthropic introduced
when they banned third-party-tool subscription OAuth.

Result: Anthropic requests from Pi bill against the Claude Max subscription
quota, not Extra Usage per-token.

See `docs/model-routing.md` §"Solution we use" for the full mechanism.

## 7. OpenAI (GPT-5.4 / Codex) — built-in to Pi, subscription routing

- **Path**: `pi /login` → ChatGPT Plus/Pro/Team → stored as OAuth in `~/.pi/agent/auth.json`
- **Officially endorsed by OpenAI**: [Codex for OSS](https://developers.openai.com/community/codex-for-oss)
- **Status**: NOT YET CONFIGURED. Run `pi /login` interactively to enable.

## 8. Google (Gemini 3.1 Pro) — built-in to Pi, no subscription path

- **Path**: `GEMINI_API_KEY` env var or `pi /login` → API key
- **Status**: NOT CONFIGURED. No subscription option exists; if added, billed
  per-token ($2/M input, $12/M output).
- **Open question**: With Anthropic on Max subscription via pi-oauth, Gemini's
  main pitch (cheap 1M context) is undercut. Worth deciding whether to bother.

## 9. R2 backup (off-site safety net) — relocated 2026-05-04

- **Was**: `~/.loom/backup/` with LaunchAgents `io.loom.backup{,-check}.plist`
- **Now**: `~/.local/share/r2-backup/` with LaunchAgents
  `io.r2-backup.hourly.plist` + `io.r2-backup.check.plist`
- **Backend**: `restic` to Cloudflare R2 (rclone backend, see
  `~/.local/share/r2-backup/env.sh` for `RCLONE_CONF` path; key material
  managed there, not in this rig)
- **Cadence**: hourly snapshot, weekly integrity check (Saturdays 04:00 local)
- **Retention**: 24h / 30d / 12w / 12m / 5y
- **What it covers** (`sources.txt`): `~/.claude-mem`, `~/.pi`, `~/.claude`,
  `~/.codex`, dotfiles, all of `~/gt`, this rig's launch agents
- **What it excludes** (`excludes.txt`): build dirs, caches, polecat sandboxes,
  `.runtime/sockets`, big media (.r3d/.braw/.mxf — backed up separately)
- **Verification**: snapshot 54 created from new location 2026-05-04 02:06 UTC
  (tag: `verify-relocation`)

## 10. Pi crash diagnostics — relocated 2026-05-04

- **Was**: `~/.loom/bin/pi-crash-logger.js` writing to `~/.loom/log/`
- **Now**: `~/.local/lib/pi-crash-logger.js` writing to `~/.local/share/pi-crash-logs/`
- Loaded into every `pi` invocation via the `pi()` shell function in `~/.zshrc`
  (`NODE_OPTIONS=--unhandled-rejections=warn --require=...pi-crash-logger.js`)
- Captures unhandled rejections + the call site of any `process.exit()` so we
  can see *why* Pi died, even when secondary `__cxa_finalize_ranges` native-addon
  teardown SIGABRTs the process before stderr can flush
- Why it exists: see mayor bd memory `crash-investigation-breadcrumb-3`

## Drift detection

```bash
# Quick check that snapshots match live
diff ~/.pi/agent/models.json   ~/gt/sovereign/crew/vivake/configs/models.json
diff ~/.pi/agent/settings.json ~/gt/sovereign/crew/vivake/configs/settings.json
diff ~/Library/LaunchAgents/com.user.mlx-lm.plist ~/gt/sovereign/crew/vivake/configs/com.user.mlx-lm.plist
diff ~/.local/bin/mlx-server-run ~/gt/sovereign/crew/vivake/scripts/mlx-server-run
diff ~/.local/bin/mlx-switch    ~/gt/sovereign/crew/vivake/scripts/mlx-switch
```

When live files change, re-snapshot and commit.

## Open action items (priority order)

1. **`pi /login` → ChatGPT Plus/Pro** — enables subscription-included GPT-5.4.
2. **Set `FIREWORKS_API_KEY` and `CEREBRAS_API_KEY`** — activates OSS-frontier
   tier (Kimi K2.6, DeepSeek V3.2, Qwen3-Coder 480B, etc.).
3. **Verify Fireworks model IDs** against live catalog at first call
   (`kimi-k2-6-instruct` and `deepseek-v3p2` are educated guesses).
4. **Decide on Gemini**: pay for `GEMINI_API_KEY` or skip Google entirely.
5. **Fleet rollout**: this rig (sovereign on this Mac Studio) is the only
   machine that's had the Loom removal + pi-oauth installation done. Other
   machines in the fleet still have the old `~/.loom/` setup. Plan and execute
   the same migration there when convenient.
