# L0 — Sovereign Compute

> **Tabula Layer 0 reference deployment.** Privacy-class routing, cold-by-default compute, model registry, and sleep API.

This directory contains the canonical reference implementation for Tabula's sovereign compute layer. It is hardware-specific where noted; each consumer adapts hardware paths, but the architecture is universal.

## Directory layout

```
l0/
├── README.md                    ← this file
├── configs/
│   ├── models.json              ← provider/model catalog (Ollama, MLX-LM, Fireworks, Cerebras, Anthropic, OpenAI, Gemini)
│   ├── settings.json            ← Pi harness settings snapshot
│   └── com.user.mlx-lm.plist    ← macOS LaunchAgent for MLX-LM (reference — adapt to your platform)
├── docs/
│   ├── model-routing.md         ← per-task routing decision tree
│   ├── router.md                ← pi-router extension spec
│   └── stack-inventory.md       ← canonical inventory of configured providers
└── scripts/
    ├── mlx-server-run           ← MLX-LM server bootstrap
    ├── mlx-switch               ← model switch helper
    └── migrate-from-loom.sh     ← Loom-to-Tabula migration
```

## Privacy-class routing

Every prompt carries a classification:

| Class | Allowed backends | Default for |
|---|---|---|
| `family_or_self` | Owned infra only | TWIN-personal default; Bower family-tier |
| `project` | Owned + cloud-OSS (Fireworks, Cerebras, Lambda) + private Claude on GCP | Luce coordinator; Bower agent calls; TWIN project-tier |
| `public` | Anything including frontier-closed | Public content; benchmarking |

The router refuses to lower the class. Misclassification surfaces as an audit anomaly.

## Provider reference

**Owned infra (primary)**
- Ollama on `:11434` — qwen3-coder:30b, qwen2.5-coder:7b, qwen2.5:72b, llama3.3:70b-q4, nomic-embed-text
- MLX-LM on `:8081` — Llama-3.3-70B-4bit, Qwen3-Coder-30B-A3B-4bit

**Cloud-OSS fallback**
- Fireworks — Kimi K2.6, DeepSeek V3.2, Qwen3-Coder 480B, GLM 4.6, Kimi K2
- Cerebras — Llama 3.3 70B, Qwen3-Coder 480B (~2000 tok/s)

**Frontier last resort**
- Anthropic Claude (OAuth via Pi harness)
- OpenAI GPT / Codex (subscription via Pi)
- Google Gemini (API key)

## Routing policy snapshot

- **Hard agentic coding** → Claude Opus 4.7 (via Loom inside GT, or Meridian outside)
- **Long-context (>200K)** → Claude Opus 4.7 or Gemini 3.1 Pro
- **Bulk OSS-grade work** → Kimi K2.6 (Fireworks)
- **High-volume cheap** → DeepSeek V3.2 (Fireworks)
- **Latency-critical** → Cerebras Llama 3.3 70B
- **Offline / private** → MLX Qwen3-Coder 30B-A3B (local)

See `docs/model-routing.md` for the full decision tree.

## Attribution

Derived from `omniscia/sovereign` (the personal sovereign-AI workspace that was one of three independent convergences on this architecture). Hardware paths and personal identifiers have been generalized.
