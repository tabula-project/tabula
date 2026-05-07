/**
 * Layer 1 routing rules.
 *
 * Ordered by priority (lowest number wins on ties). First matching rule fires.
 *
 * Models are referenced by canonical "provider/id" form. The router maps these
 * to actual Model objects via ctx.modelRegistry.find().
 *
 * These rules are starting heuristics. The feedback loop and stats will reveal
 * which rules misfire; adjust here as data accumulates.
 *
 * OSS-first philosophy: default to Kimi K2.6 (Fireworks). Escalate to commercial
 * (Anthropic Opus) only when a specific signal demands it.
 */

import type { Rule } from "./types.js";

// Canonical IDs as Pi expects them. Keep these in sync with ~/.pi/agent/models.json
// and the live provider catalogs. Commercial fallbacks are subscription-routed.
export const MODELS = {
	// OSS frontier
	KIMI_K2_6: "fireworks/accounts/fireworks/models/kimi-k2p6",
	KIMI_K2_5: "fireworks/accounts/fireworks/models/kimi-k2p5",
	KIMI_K2_THINKING: "fireworks/accounts/fireworks/models/kimi-k2-thinking",
	DEEPSEEK_V4_PRO: "fireworks/accounts/fireworks/models/deepseek-v4-pro",
	GLM_5_1: "fireworks/accounts/fireworks/models/glm-5p1",
	GLM_5: "fireworks/accounts/fireworks/models/glm-5",
	// Cerebras free
	QWEN_3_235B: "cerebras/qwen-3-235b-a22b-instruct-2507",
	// Commercial frontier (escalation)
	OPUS_4_7: "anthropic/claude-opus-4-7",
	OPUS_4_6: "anthropic/claude-opus-4-6",
	GPT_5_4_CODEX: "openai-codex/gpt-5.4-codex",
	GPT_5_4: "openai-codex/gpt-5.4",
	// Local (offline mode)
	MLX_QWEN_CODER: "mlx/mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
	MLX_LLAMA_70B: "mlx/mlx-community/Llama-3.3-70B-Instruct-4bit",
	OLLAMA_QWEN_CODER: "ollama/qwen3-coder:30b",
} as const;

export const DEFAULT_MODEL = MODELS.KIMI_K2_6;
export const DEFAULT_LOCAL_MODEL = MODELS.MLX_QWEN_CODER;

export const RULES: Rule[] = [
	// ---------------- OFFLINE MODE ----------------
	// Highest priority: when offline, restrict to local models.
	{
		name: "offline-code",
		priority: 0,
		when: (f) => !f.online && (f.hasCodeFences || f.touchedFiles > 0 || f.mentionsDebug || f.mentionsRefactor),
		pick: MODELS.MLX_QWEN_CODER,
		reason: "offline · code-task → local MLX coder",
	},
	{
		name: "offline-default",
		priority: 1,
		when: (f) => !f.online,
		pick: MODELS.MLX_LLAMA_70B,
		reason: "offline → local MLX general",
	},

	// ---------------- ESCALATION (commercial) ----------------
	// Concurrency bugs are notoriously hard. Escalate to Opus.
	{
		name: "concurrency-bug",
		priority: 10,
		when: (f) => f.mentionsConcurrency,
		pick: MODELS.OPUS_4_7,
		reason: "concurrency-related → escalate to Opus 4.7",
	},
	// Very long contexts: Opus 4.6 has 1M; DeepSeek V4 Pro has 1M and is cheaper.
	// Default to OSS for OSS-first, escalate to commercial only on hardest tasks.
	{
		name: "huge-context-oss",
		priority: 20,
		when: (f) => f.conversationTokensApprox > 200_000,
		pick: MODELS.DEEPSEEK_V4_PRO,
		reason: "ctx > 200K → DeepSeek V4 Pro (1M)",
	},

	// ---------------- OSS REASONING ----------------
	{
		name: "reasoning-heavy",
		priority: 30,
		when: (f) => f.mentionsReasoningHeavy,
		pick: MODELS.GLM_5_1,
		reason: "formal-reasoning → GLM 5.1 (reasoning OSS)",
	},
	{
		name: "deep-debug",
		priority: 35,
		when: (f) => f.mentionsDebug && f.conversationTokensApprox > 50_000,
		pick: MODELS.KIMI_K2_THINKING,
		reason: "debug + large ctx → Kimi K2 Thinking",
	},

	// ---------------- FAST/FREE (Cerebras) ----------------
	// Cerebras Free tier rate-limits aggressively. Only route here when the
	// task is GENUINELY trivial — single-word lookups, yes/no questions, etc.
	// Anything substantive should go through the OSS-frontier Kimi K2.6 default.
	{
		name: "trivial-lookup",
		priority: 40,
		when: (f) =>
			f.promptTokensApprox < 50 &&
			!f.hasCodeFences &&
			f.touchedFiles === 0 &&
			!f.mentionsDebug &&
			!f.mentionsRefactor &&
			!f.mentionsExplain &&
			f.conversationTokensApprox < 500,
		pick: MODELS.QWEN_3_235B,
		reason: "trivial lookup (<50 tok, fresh ctx) → Cerebras free",
	},

	// ---------------- COST-OPTIMIZED OSS ----------------
	{
		name: "code-explain",
		priority: 50,
		when: (f) => f.mentionsExplain && f.hasCodeFences,
		pick: MODELS.KIMI_K2_5,
		reason: "explain code → Kimi K2.5 (cheaper)",
	},
	{
		name: "multi-file-refactor",
		priority: 55,
		when: (f) => f.touchedFiles >= 5 || f.mentionsRefactor,
		pick: MODELS.DEEPSEEK_V4_PRO,
		reason: "multi-file or refactor → DeepSeek V4 Pro (long ctx, $1.74/$3.48)",
	},

	// ---------------- DEFAULT ----------------
	{
		name: "default",
		priority: 1000,
		when: () => true,
		pick: DEFAULT_MODEL,
		reason: "default → Kimi K2.6 (OSS-first)",
	},
];

export function pickRule(features: import("./types.js").Features): Rule {
	const sorted = [...RULES].sort((a, b) => a.priority - b.priority);
	for (const rule of sorted) {
		if (rule.when(features)) return rule;
	}
	return sorted[sorted.length - 1]!;
}

export function parseCanonical(canonical: string): { provider: string; id: string } {
	const slash = canonical.indexOf("/");
	if (slash < 0) return { provider: canonical, id: "" };
	return { provider: canonical.slice(0, slash), id: canonical.slice(slash + 1) };
}
