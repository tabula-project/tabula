/**
 * Feature extraction from a turn's prompt + context.
 *
 * Cheap heuristics. No model calls. Aim for ~5-10ms per call.
 */

import { createHash } from "node:crypto";
import { basename, dirname, join } from "node:path";
import { existsSync } from "node:fs";
import type { Features } from "./types.js";
import { isOnline } from "./network.js";

/** Approximate token count by char/4 — close enough for routing decisions. */
function approxTokens(text: string): number {
	return Math.ceil((text?.length ?? 0) / 4);
}

function hasCodeFences(text: string): boolean {
	return /```[\s\S]+?```/.test(text) || /^\s{4,}\S/m.test(text);
}

const RX_CONCURRENCY = /\b(race condition|deadlock|concurrency|memory[- ]?leak|atomic|lock-free|TOCTOU|thread[- ]safe|synchron(?:ize|ization))\b/i;
const RX_REASONING_HEAVY = /\b(prove|theorem|lemma|complexity analysis|big[- ]?[oO]\b|invariant|formal verification|reduction)\b/i;
const RX_EXPLAIN = /\b(explain|describe|summarize|summary|walk[- ]?through|tldr|tl;dr)\b/i;
const RX_REFACTOR = /\b(refactor|rewrite|reorganize|extract|inline|rename|migrate)\b/i;
const RX_DEBUG = /\b(debug|fix|bug|error|crash|fail(?:ing|ed|s)?|broken|wrong|broke|regression|stack trace|stacktrace)\b/i;

const FILE_REF_RX = /\b[\w./-]+\.(ts|tsx|js|jsx|py|go|rs|c|cc|cpp|h|hpp|md|json|yaml|yml|toml|sh|sql|html|css|scss)\b/g;

/** Detect rig name by walking up from cwd looking for rigs.json marker. */
export function detectRig(cwd: string): string {
	let dir = cwd;
	let prev = cwd;
	while (true) {
		if (existsSync(join(dir, "rigs.json"))) {
			if (prev === dir) return basename(cwd);
			return basename(prev);
		}
		const parent = dirname(dir);
		if (parent === dir) break;
		prev = dir;
		dir = parent;
	}
	return basename(cwd);
}

export interface BuildFeaturesInput {
	prompt: string;
	conversationText?: string; // concatenated prior turns for token-count purposes
	systemPrompt?: string;
	cwd: string;
	toolsLoaded: number;
	hasImages: boolean;
}

export function buildFeatures(input: BuildFeaturesInput): Features {
	const promptTokens = approxTokens(input.prompt);
	const convTokens = approxTokens(input.conversationText ?? "");
	const sysHash = input.systemPrompt
		? createHash("sha256").update(input.systemPrompt).digest("hex").slice(0, 12)
		: "";

	const fileRefs = (input.prompt.match(FILE_REF_RX) ?? []).length;

	return {
		promptText: input.prompt,
		promptTokensApprox: promptTokens,
		conversationTokensApprox: convTokens + promptTokens,
		hasCodeFences: hasCodeFences(input.prompt),
		hasImages: input.hasImages,
		mentionsConcurrency: RX_CONCURRENCY.test(input.prompt),
		mentionsReasoningHeavy: RX_REASONING_HEAVY.test(input.prompt),
		mentionsExplain: RX_EXPLAIN.test(input.prompt),
		mentionsRefactor: RX_REFACTOR.test(input.prompt),
		mentionsDebug: RX_DEBUG.test(input.prompt),
		touchedFiles: fileRefs,
		rigName: detectRig(input.cwd),
		cwd: input.cwd,
		systemPromptHash: sysHash,
		toolsLoaded: input.toolsLoaded,
		online: isOnline(),
	};
}

export function summarizeFeatures(f: Features): string {
	const parts: string[] = [];
	parts.push(`${f.promptTokensApprox} prompt-tok`);
	parts.push(`${f.conversationTokensApprox} ctx-tok`);
	if (f.hasCodeFences) parts.push("code");
	if (f.hasImages) parts.push("img");
	if (f.mentionsConcurrency) parts.push("concurrency");
	if (f.mentionsReasoningHeavy) parts.push("reasoning");
	if (f.mentionsExplain) parts.push("explain");
	if (f.mentionsRefactor) parts.push("refactor");
	if (f.mentionsDebug) parts.push("debug");
	if (f.touchedFiles > 0) parts.push(`${f.touchedFiles} files`);
	parts.push(`rig:${f.rigName}`);
	parts.push(f.online ? "online" : "OFFLINE");
	return parts.join(" · ");
}
