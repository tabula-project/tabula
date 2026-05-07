/**
 * Shared types for pi-router.
 */

export type VerbosityLevel = "debug" | "always" | "escalations" | "quiet" | "silent";

export type DecisionLayer = "rules" | "embeddings" | "llm" | "fallback" | "manual-override";

export interface RouterConfig {
	verbosity: VerbosityLevel;
	defaultModel: string; // canonical "provider/id" form
	offlineCacheSecs: number;
}

export interface Features {
	promptText: string;
	promptTokensApprox: number; // rough char/4 estimate
	conversationTokensApprox: number;
	hasCodeFences: boolean;
	hasImages: boolean;
	mentionsConcurrency: boolean;
	mentionsReasoningHeavy: boolean;
	mentionsExplain: boolean;
	mentionsRefactor: boolean;
	mentionsDebug: boolean;
	touchedFiles: number; // count of file refs in conversation
	rigName: string; // basename of cwd's rig (e.g., "sovereign")
	cwd: string;
	systemPromptHash: string;
	toolsLoaded: number;
	online: boolean;
}

export interface RoutingDecision {
	turnId: string; // unique per-turn id
	timestamp: number; // ms since epoch
	chosenProvider: string;
	chosenModel: string;
	canonicalChosen: string; // "provider/model"
	layer: DecisionLayer;
	ruleName?: string;
	reason: string;
	alternativesConsidered: Array<{ canonical: string; score?: number; reason?: string }>;
	features: Features;
	feedback?: "good" | "bad";
	feedbackReason?: string;
	feedbackAt?: number;
	finalCost?: number;
	finalTokensIn?: number;
	finalTokensOut?: number;
}

export interface Rule {
	name: string;
	when: (f: Features) => boolean;
	pick: string; // "provider/id" canonical
	reason: string;
	priority: number;
}
