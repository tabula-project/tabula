/**
 * Persistent store for routing decisions and feedback.
 *
 * SQLite at ~/.local/share/pi-router/decisions.db. Survives Pi restarts;
 * accumulates training signal over time for v0.5+ embedding-based routing.
 */

import Database from "better-sqlite3";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { RoutingDecision } from "./types.js";

const STORE_DIR = join(homedir(), ".local", "share", "pi-router");
const DB_PATH = join(STORE_DIR, "decisions.db");

let db: Database.Database | null = null;

function getDb(): Database.Database {
	if (db) return db;
	mkdirSync(STORE_DIR, { recursive: true });
	db = new Database(DB_PATH);
	db.pragma("journal_mode = WAL");
	db.pragma("synchronous = NORMAL");
	db.exec(`
    CREATE TABLE IF NOT EXISTS decisions (
      turn_id TEXT PRIMARY KEY,
      timestamp INTEGER NOT NULL,
      chosen_provider TEXT NOT NULL,
      chosen_model TEXT NOT NULL,
      canonical_chosen TEXT NOT NULL,
      layer TEXT NOT NULL,
      rule_name TEXT,
      reason TEXT,
      features_json TEXT NOT NULL,
      alternatives_json TEXT,
      feedback TEXT,
      feedback_reason TEXT,
      feedback_at INTEGER,
      final_cost REAL,
      final_tokens_in INTEGER,
      final_tokens_out INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(timestamp);
    CREATE INDEX IF NOT EXISTS idx_decisions_canonical ON decisions(canonical_chosen);
    CREATE INDEX IF NOT EXISTS idx_decisions_feedback ON decisions(feedback);
  `);
	return db;
}

export function recordDecision(d: RoutingDecision): void {
	const stmt = getDb().prepare(`
    INSERT OR REPLACE INTO decisions
      (turn_id, timestamp, chosen_provider, chosen_model, canonical_chosen,
       layer, rule_name, reason, features_json, alternatives_json,
       feedback, feedback_reason, feedback_at,
       final_cost, final_tokens_in, final_tokens_out)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
	stmt.run(
		d.turnId,
		d.timestamp,
		d.chosenProvider,
		d.chosenModel,
		d.canonicalChosen,
		d.layer,
		d.ruleName ?? null,
		d.reason,
		JSON.stringify(d.features),
		JSON.stringify(d.alternativesConsidered),
		d.feedback ?? null,
		d.feedbackReason ?? null,
		d.feedbackAt ?? null,
		d.finalCost ?? null,
		d.finalTokensIn ?? null,
		d.finalTokensOut ?? null,
	);
}

export function applyFeedback(turnId: string, feedback: "good" | "bad", reason?: string): boolean {
	const stmt = getDb().prepare(`
    UPDATE decisions
    SET feedback = ?, feedback_reason = ?, feedback_at = ?
    WHERE turn_id = ?
  `);
	const result = stmt.run(feedback, reason ?? null, Date.now(), turnId);
	return result.changes > 0;
}

export function lastDecision(): RoutingDecision | null {
	const row = getDb().prepare(`SELECT * FROM decisions ORDER BY timestamp DESC LIMIT 1`).get() as
		| Record<string, unknown>
		| undefined;
	if (!row) return null;
	return rowToDecision(row);
}

export function getDecision(turnId: string): RoutingDecision | null {
	const row = getDb().prepare(`SELECT * FROM decisions WHERE turn_id = ?`).get(turnId) as
		| Record<string, unknown>
		| undefined;
	if (!row) return null;
	return rowToDecision(row);
}

export interface StatsSummary {
	totalTurns: number;
	withFeedback: number;
	good: number;
	bad: number;
	byModel: Array<{ model: string; turns: number; good: number; bad: number; goodPct: number; avgCost: number }>;
	byLayer: Array<{ layer: string; count: number; goodPct: number }>;
	byRule: Array<{ rule: string; count: number; goodPct: number }>;
}

export function statsSummary(sinceMs?: number): StatsSummary {
	const since = sinceMs ?? 0;
	const dbi = getDb();
	const total = dbi.prepare(`SELECT COUNT(*) AS c FROM decisions WHERE timestamp >= ?`).get(since) as { c: number };
	const fb = dbi
		.prepare(`SELECT COUNT(*) AS c FROM decisions WHERE timestamp >= ? AND feedback IS NOT NULL`)
		.get(since) as { c: number };
	const good = dbi
		.prepare(`SELECT COUNT(*) AS c FROM decisions WHERE timestamp >= ? AND feedback = 'good'`)
		.get(since) as { c: number };
	const bad = dbi
		.prepare(`SELECT COUNT(*) AS c FROM decisions WHERE timestamp >= ? AND feedback = 'bad'`)
		.get(since) as { c: number };

	const byModelRows = dbi
		.prepare(`
      SELECT canonical_chosen AS model,
             COUNT(*) AS turns,
             SUM(CASE WHEN feedback='good' THEN 1 ELSE 0 END) AS good,
             SUM(CASE WHEN feedback='bad' THEN 1 ELSE 0 END) AS bad,
             AVG(COALESCE(final_cost, 0)) AS avg_cost
      FROM decisions
      WHERE timestamp >= ?
      GROUP BY canonical_chosen
      ORDER BY turns DESC
    `)
		.all(since) as Array<{ model: string; turns: number; good: number; bad: number; avg_cost: number }>;

	const byLayerRows = dbi
		.prepare(`
      SELECT layer,
             COUNT(*) AS count,
             SUM(CASE WHEN feedback='good' THEN 1 ELSE 0 END) AS good,
             SUM(CASE WHEN feedback IS NOT NULL THEN 1 ELSE 0 END) AS rated
      FROM decisions
      WHERE timestamp >= ?
      GROUP BY layer
    `)
		.all(since) as Array<{ layer: string; count: number; good: number; rated: number }>;

	const byRuleRows = dbi
		.prepare(`
      SELECT rule_name AS rule,
             COUNT(*) AS count,
             SUM(CASE WHEN feedback='good' THEN 1 ELSE 0 END) AS good,
             SUM(CASE WHEN feedback IS NOT NULL THEN 1 ELSE 0 END) AS rated
      FROM decisions
      WHERE timestamp >= ? AND rule_name IS NOT NULL
      GROUP BY rule_name
      ORDER BY count DESC
    `)
		.all(since) as Array<{ rule: string; count: number; good: number; rated: number }>;

	return {
		totalTurns: total.c,
		withFeedback: fb.c,
		good: good.c,
		bad: bad.c,
		byModel: byModelRows.map((r) => ({
			model: r.model,
			turns: r.turns,
			good: r.good,
			bad: r.bad,
			goodPct: r.good + r.bad === 0 ? 0 : Math.round((r.good / (r.good + r.bad)) * 100),
			avgCost: Number((r.avg_cost ?? 0).toFixed(4)),
		})),
		byLayer: byLayerRows.map((r) => ({
			layer: r.layer,
			count: r.count,
			goodPct: r.rated === 0 ? 0 : Math.round((r.good / r.rated) * 100),
		})),
		byRule: byRuleRows.map((r) => ({
			rule: r.rule,
			count: r.count,
			goodPct: r.rated === 0 ? 0 : Math.round((r.good / r.rated) * 100),
		})),
	};
}

function rowToDecision(row: Record<string, unknown>): RoutingDecision {
	return {
		turnId: row.turn_id as string,
		timestamp: row.timestamp as number,
		chosenProvider: row.chosen_provider as string,
		chosenModel: row.chosen_model as string,
		canonicalChosen: row.canonical_chosen as string,
		layer: row.layer as RoutingDecision["layer"],
		ruleName: (row.rule_name as string | null) ?? undefined,
		reason: (row.reason as string) ?? "",
		features: JSON.parse(row.features_json as string),
		alternativesConsidered: row.alternatives_json ? JSON.parse(row.alternatives_json as string) : [],
		feedback: (row.feedback as RoutingDecision["feedback"]) ?? undefined,
		feedbackReason: (row.feedback_reason as string | null) ?? undefined,
		feedbackAt: (row.feedback_at as number | null) ?? undefined,
		finalCost: (row.final_cost as number | null) ?? undefined,
		finalTokensIn: (row.final_tokens_in as number | null) ?? undefined,
		finalTokensOut: (row.final_tokens_out as number | null) ?? undefined,
	};
}

export function dbPath(): string {
	return DB_PATH;
}
