/**
 * pi-router — local-first adaptive model router for Pi.
 *
 * v0 capabilities:
 *   - Layer 1 heuristic rules (rules.ts) — pure code, no model
 *   - Offline detection — passive, restricts catalog to local providers
 *   - Auto-fallback to local on cloud network failure
 *   - Feedback capture via Shift+Ctrl+G / Shift+Ctrl+B + /good /bad slash commands
 *   - Persistent SQLite store at ~/.local/share/pi-router/decisions.db
 *   - /router-stats — aggregate dashboard
 *   - /router-explain <turnId> — show why a past decision was made
 *   - /router-verbose <level> — toggle verbosity
 *   - Manual override detection — Ctrl+P cycle marks decision as user-driven
 *
 * Future:
 *   v0.5 — Layer 2: nearest-neighbor on prompt embeddings (nomic-embed-text local)
 *   v1   — Layer 3: LLM tiebreaker via local qwen2.5-coder:7b
 *
 * No external dependencies for routing decisions. Everything runs on-device.
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import type { Model, Api } from "@mariozechner/pi-ai";
import { randomUUID } from "node:crypto";
import { buildFeatures, summarizeFeatures } from "./features.js";
import { isOnline, isLocalProvider, maybeMarkOfflineFromError, offlineRemainingMs } from "./network.js";
import { pickRule, parseCanonical, MODELS, DEFAULT_LOCAL_MODEL, RULES } from "./rules.js";
import { isThrottled, markThrottled, detectThrottleFromError, throttleRemainingMs } from "./throttle.js";
import { recordDecision, applyFeedback, lastDecision, getDecision, statsSummary, dbPath } from "./store.js";
import { loadConfig, saveConfig, configPath } from "./config-io.js";
import type { RoutingDecision, VerbosityLevel } from "./types.js";

export default function piRouterExtension(pi: ExtensionAPI) {
	let config = loadConfig();
	let pendingDecision: RoutingDecision | null = null; // captured before agent runs, finalized after
	let lastManualSelectAt = 0; // timestamp of last user-driven model select
	let routerEnabled = true;

	// ----------------------------- model_select ---------------------------------
	// Watch for user-driven model changes. If the user pressed Ctrl+P or used
	// /model, we mark them so the next before_agent_start respects the choice
	// instead of overriding it.
	pi.on("model_select", (event, _ctx) => {
		if (event.source === "cycle" || event.source === "set") {
			lastManualSelectAt = Date.now();
		}
	});

	// ----------------------------- before_agent_start ---------------------------
	// This is where routing actually happens. Pi has the prompt; we extract
	// features, pick a model, and call pi.setModel() before the LLM call goes out.
	pi.on("before_agent_start", async (event, ctx) => {
		if (!routerEnabled) return;

		// If user manually overrode the model recently (within last 5s), respect it.
		const overrideAge = Date.now() - lastManualSelectAt;
		const isManualOverride = overrideAge < 5000 && lastManualSelectAt > 0;

		// Build features from the payload
		const features = buildFeatures({
			prompt: event.prompt ?? "",
			conversationText: undefined, // could pass conversation history if accessible
			systemPrompt: event.systemPrompt,
			cwd: ctx.cwd ?? process.cwd(),
			toolsLoaded: event.systemPromptOptions?.selectedTools?.length ?? 0,
			hasImages: (event.images ?? []).length > 0,
		});

		const turnId = randomUUID();

		if (isManualOverride) {
			// Record the manual override as a decision; do not change the model.
			// We can't easily read the current model from the registry; use "unknown"
			// and let the user inspect the actual chosen model in the inline message.
			const decision: RoutingDecision = {
				turnId,
				timestamp: Date.now(),
				chosenProvider: "manual",
				chosenModel: "manual",
				canonicalChosen: "manual/override",
				layer: "manual-override",
				ruleName: undefined,
				reason: "user manually selected this model (Ctrl+P or /model)",
				alternativesConsidered: [],
				features,
			};
			recordDecision(decision);
			pendingDecision = decision;
			renderInline(pi, decision, config.verbosity, true);
			updateStatusBar(pi, ctx, decision);
			lastManualSelectAt = 0;
			return;
		}

		// Layer 1: rules
		// Skip rules whose pick resolves to a currently-throttled provider.
		const sortedRules = [...RULES].sort((a, b) => a.priority - b.priority);
		let rule = pickRule(features);
		for (const candidate of sortedRules) {
			if (!candidate.when(features)) continue;
			const { provider: p } = parseCanonical(candidate.pick);
			if (isThrottled(p)) continue; // skip throttled providers
			rule = candidate;
			break;
		}
		const { provider, id } = parseCanonical(rule.pick);

		// Try to resolve the chosen model in Pi's registry
		const modelRegistry = ctx.modelRegistry;
		let chosen: Model<Api> | undefined = modelRegistry?.find?.(provider, id);
		let chosenCanonical = rule.pick;
		let chosenLayer: RoutingDecision["layer"] = "rules";
		let chosenReason = rule.reason;
		const alternatives: RoutingDecision["alternativesConsidered"] = [];

		// If the model isn't available (e.g., offline and we picked a cloud model,
		// or no API key), fall back to the local default.
		if (!chosen) {
			alternatives.push({
				canonical: rule.pick,
				reason: `unavailable in registry (offline=${!features.online} or no auth)`,
			});
			const fallback = parseCanonical(DEFAULT_LOCAL_MODEL);
			chosen = modelRegistry?.find?.(fallback.provider, fallback.id);
			chosenCanonical = DEFAULT_LOCAL_MODEL;
			chosenLayer = "fallback";
			chosenReason = `${rule.name} target unreachable → local fallback`;
		}

		// If even the local fallback isn't available, give up routing for this turn
		if (!chosen) {
			pi.sendMessage(
				{
					customType: "pi-router",
					content: `⚠ pi-router: no eligible model available (rule: ${rule.name}, fallback: ${DEFAULT_LOCAL_MODEL}). Using whatever Pi has set.`,
					display: true,
				},
				{ deliverAs: "nextTurn" },
			);
			return;
		}

		// Apply the model selection
		const success = await pi.setModel(chosen);
		if (!success) {
			alternatives.push({ canonical: chosenCanonical, reason: "setModel returned false (no auth?)" });
			// Try local fallback if we weren't already
			if (chosenLayer !== "fallback") {
				const fallback = parseCanonical(DEFAULT_LOCAL_MODEL);
				const fbModel = modelRegistry?.find?.(fallback.provider, fallback.id);
				if (fbModel) {
					await pi.setModel(fbModel);
					chosen = fbModel;
					chosenCanonical = DEFAULT_LOCAL_MODEL;
					chosenLayer = "fallback";
					chosenReason = `setModel failed for ${rule.pick} → local fallback`;
				}
			}
		}

		const decision: RoutingDecision = {
			turnId,
			timestamp: Date.now(),
			chosenProvider: chosen.provider,
			chosenModel: chosen.id,
			canonicalChosen: chosenCanonical,
			layer: chosenLayer,
			ruleName: rule.name,
			reason: chosenReason,
			alternativesConsidered: alternatives,
			features,
		};
		recordDecision(decision);
		pendingDecision = decision;

		renderInline(pi, decision, config.verbosity, false);
		updateStatusBar(pi, ctx, decision);
	});

	// ----------------------------- after_provider_response ----------------------
	// Listen for network errors → mark offline so future decisions route local.
	pi.on("after_provider_response", (event, _ctx) => {
		const err = (event as unknown as { error?: unknown }).error;
		if (!err) return;

		// Network error → offline mode
		const wasNetwork = maybeMarkOfflineFromError(err);
		if (wasNetwork && config.verbosity !== "silent") {
			pi.sendMessage(
				{
					customType: "pi-router",
					content: `⚠ pi-router: network error detected, entering OFFLINE mode for ${Math.round(offlineRemainingMs() / 1000)}s. Future routes will pick local models.`,
					display: true,
				},
				{ deliverAs: "nextTurn" },
			);
			return;
		}

		// Rate limit / server error → throttle the provider
		const throttleKind = detectThrottleFromError(err);
		if (throttleKind && pendingDecision) {
			const provider = pendingDecision.chosenProvider;
			markThrottled(provider);
			if (config.verbosity !== "silent") {
				pi.sendMessage(
					{
						customType: "pi-router",
						content: `⚠ pi-router: ${provider} ${throttleKind} — throttled for ${Math.round(throttleRemainingMs(provider) / 60_000)}m. Next routes will skip this provider.`,
						display: true,
					},
					{ deliverAs: "nextTurn" },
				);
			}
		}
	});

	// ----------------------------- shortcuts: feedback --------------------------
	pi.registerShortcut("shift+ctrl+g", {
		description: "Mark last router decision as GOOD",
		handler: async (ctx) => {
			const last = pendingDecision ?? lastDecision();
			if (!last) {
				ctx.ui.notify("No router decision to grade", "info");
				return;
			}
			const ok = applyFeedback(last.turnId, "good");
			ctx.ui.notify(ok ? `✓ marked good: ${last.canonicalChosen}` : "Failed to record feedback", ok ? "info" : "error");
		},
	});

	pi.registerShortcut("shift+ctrl+b", {
		description: "Mark last router decision as BAD",
		handler: async (ctx) => {
			const last = pendingDecision ?? lastDecision();
			if (!last) {
				ctx.ui.notify("No router decision to grade", "info");
				return;
			}
			const ok = applyFeedback(last.turnId, "bad");
			ctx.ui.notify(ok ? `✗ marked bad: ${last.canonicalChosen}` : "Failed to record feedback", ok ? "info" : "error");
		},
	});

	// ----------------------------- commands -------------------------------------
	pi.registerCommand("good", {
		description: "Mark last router decision as good (optionally with reason: /good <reason>)",
		handler: async (args, ctx) => {
			const last = pendingDecision ?? lastDecision();
			if (!last) {
				ctx.ui.notify("No router decision to grade", "info");
				return;
			}
			const ok = applyFeedback(last.turnId, "good", args.trim() || undefined);
			ctx.ui.notify(ok ? `✓ good: ${last.canonicalChosen}${args ? ` (${args.trim()})` : ""}` : "Failed", ok ? "info" : "error");
		},
	});

	pi.registerCommand("bad", {
		description: "Mark last router decision as bad (optionally with reason: /bad <reason>)",
		handler: async (args, ctx) => {
			const last = pendingDecision ?? lastDecision();
			if (!last) {
				ctx.ui.notify("No router decision to grade", "info");
				return;
			}
			const ok = applyFeedback(last.turnId, "bad", args.trim() || undefined);
			ctx.ui.notify(ok ? `✗ bad: ${last.canonicalChosen}${args ? ` (${args.trim()})` : ""}` : "Failed", ok ? "info" : "error");
		},
	});

	pi.registerCommand("router-stats", {
		description: "Show aggregate routing decisions and feedback",
		handler: async (args, ctx) => {
			const days = parseInt(args.trim(), 10) || 30;
			const since = Date.now() - days * 24 * 3600 * 1000;
			const s = statsSummary(since);
			const lines: string[] = [];
			lines.push(`# pi-router stats — last ${days} days`);
			lines.push("");
			lines.push(`Total turns: ${s.totalTurns}`);
			lines.push(`Feedback rate: ${s.totalTurns === 0 ? 0 : Math.round((s.withFeedback / s.totalTurns) * 100)}% (${s.withFeedback}/${s.totalTurns})`);
			lines.push(`Good: ${s.good}  Bad: ${s.bad}`);
			lines.push("");
			lines.push("## By model");
			lines.push("| model | turns | good | bad | good% | avg-cost |");
			lines.push("|---|---:|---:|---:|---:|---:|");
			for (const m of s.byModel) lines.push(`| ${m.model} | ${m.turns} | ${m.good} | ${m.bad} | ${m.goodPct}% | $${m.avgCost.toFixed(4)} |`);
			lines.push("");
			lines.push("## By rule");
			lines.push("| rule | count | good% |");
			lines.push("|---|---:|---:|");
			for (const r of s.byRule) lines.push(`| ${r.rule} | ${r.count} | ${r.goodPct}% |`);
			lines.push("");
			lines.push("## By layer");
			lines.push("| layer | count | good% |");
			lines.push("|---|---:|---:|");
			for (const l of s.byLayer) lines.push(`| ${l.layer} | ${l.count} | ${l.goodPct}% |`);

			pi.sendMessage(
				{
					customType: "pi-router-stats",
					content: lines.join("\n"),
					display: true,
				},
				{ deliverAs: "nextTurn" },
			);
		},
	});

	pi.registerCommand("router-explain", {
		description: "Show why a past routing decision was made (latest if no arg, or /router-explain <turnId>)",
		handler: async (args, ctx) => {
			const id = args.trim();
			const dec = id ? getDecision(id) : lastDecision();
			if (!dec) {
				ctx.ui.notify("No matching decision found", "info");
				return;
			}
			const ageS = Math.round((Date.now() - dec.timestamp) / 1000);
			const lines = [
				`# Routing decision (${ageS}s ago)`,
				"",
				`- Turn id: \`${dec.turnId}\``,
				`- Chosen: \`${dec.canonicalChosen}\``,
				`- Layer: ${dec.layer}${dec.ruleName ? ` (rule: ${dec.ruleName})` : ""}`,
				`- Reason: ${dec.reason}`,
				`- Features: ${summarizeFeatures(dec.features)}`,
				`- Alternatives considered: ${dec.alternativesConsidered.length === 0 ? "none" : dec.alternativesConsidered.map((a) => `${a.canonical}${a.reason ? ` (${a.reason})` : ""}`).join(", ")}`,
				dec.feedback ? `- Feedback: ${dec.feedback}${dec.feedbackReason ? ` — ${dec.feedbackReason}` : ""}` : "- Feedback: none yet",
			];
			pi.sendMessage(
				{
					customType: "pi-router-explain",
					content: lines.join("\n"),
					display: true,
				},
				{ deliverAs: "nextTurn" },
			);
		},
	});

	pi.registerCommand("router-verbose", {
		description: "Set router verbosity: /router-verbose <debug|always|escalations|quiet|silent>",
		handler: async (args, ctx) => {
			const level = args.trim() as VerbosityLevel;
			const valid: VerbosityLevel[] = ["debug", "always", "escalations", "quiet", "silent"];
			if (!valid.includes(level)) {
				ctx.ui.notify(`Usage: /router-verbose <${valid.join("|")}>`, "warning");
				return;
			}
			config = { ...config, verbosity: level };
			saveConfig(config);
			ctx.ui.notify(`router verbosity → ${level}`, "info");
		},
	});

	pi.registerCommand("router-disable", {
		description: "Disable the router for this session (manual model selection only)",
		handler: async (_args, ctx) => {
			routerEnabled = false;
			ctx.ui.setStatus("pi-router", "router:disabled");
			ctx.ui.notify("pi-router disabled for this session", "info");
		},
	});

	pi.registerCommand("router-enable", {
		description: "Re-enable the router for this session",
		handler: async (_args, ctx) => {
			routerEnabled = true;
			ctx.ui.setStatus("pi-router", "router:active");
			ctx.ui.notify("pi-router enabled", "info");
		},
	});

	pi.registerCommand("router-where", {
		description: "Show where pi-router stores its data",
		handler: async (_args, ctx) => {
			pi.sendMessage(
				{
					customType: "pi-router",
					content: `**pi-router storage**\n\n- Decisions DB: \`${dbPath()}\`\n- Config:       \`${configPath()}\`\n- Verbosity:    ${config.verbosity}\n- Router state: ${routerEnabled ? "enabled" : "disabled"}`,
					display: true,
				},
				{ deliverAs: "nextTurn" },
			);
		},
	});

	// Initial status bar
	pi.on("session_start", (_event, ctx) => {
		ctx.ui.setStatus("pi-router", `router:active · verbosity:${config.verbosity}`);
	});
}

// ============================== rendering helpers ============================

function isDefaultModel(canonical: string): boolean {
	return canonical === MODELS.KIMI_K2_6;
}

function shouldShowInline(decision: RoutingDecision, verbosity: VerbosityLevel): boolean {
	if (verbosity === "silent" || verbosity === "quiet") return false;
	if (verbosity === "debug" || verbosity === "always") return true;
	// "escalations" — only show when not default
	return !isDefaultModel(decision.canonicalChosen);
}

function renderInline(pi: ExtensionAPI, decision: RoutingDecision, verbosity: VerbosityLevel, isOverride: boolean): void {
	if (!shouldShowInline(decision, verbosity)) return;

	const lines: string[] = [];
	if (isOverride) {
		lines.push(`🧑 manual override: \`${decision.canonicalChosen}\``);
	} else {
		lines.push(`🔀 routed to \`${decision.canonicalChosen}\``);
		lines.push(`   layer:${decision.layer}${decision.ruleName ? ` rule:${decision.ruleName}` : ""}`);
		lines.push(`   reason: ${decision.reason}`);
	}

	if (verbosity === "debug") {
		lines.push(`   features: ${summarizeFeatures(decision.features)}`);
		if (decision.alternativesConsidered.length > 0) {
			lines.push(
				`   alternatives: ${decision.alternativesConsidered
					.map((a) => `${a.canonical}${a.reason ? ` (${a.reason})` : ""}`)
					.join(", ")}`,
			);
		}
		lines.push(`   turn-id: ${decision.turnId.slice(0, 8)}`);
		lines.push(`   feedback: Shift+Ctrl+G good · Shift+Ctrl+B bad · /good <reason> · /bad <reason>`);
	} else if (verbosity === "always") {
		// brief
	}

	pi.sendMessage(
		{
			customType: "pi-router",
			content: lines.join("\n"),
			display: true,
		},
		{ deliverAs: "nextTurn" },
	);
}

function updateStatusBar(pi: ExtensionAPI, ctx: { ui: { setStatus: (k: string, v: string) => void } }, decision: RoutingDecision): void {
	const offline = isOnline() ? "" : " · OFFLINE";
	const local = isLocalProvider(decision.chosenProvider) ? "📍" : "";
	ctx.ui.setStatus(
		"pi-router",
		`router:${decision.layer}${offline} · last:${local}${decision.canonicalChosen.split("/").pop()}`,
	);
}
