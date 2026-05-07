/**
 * Persistent router config (verbosity, etc.) at ~/.local/share/pi-router/config.json.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { RouterConfig } from "./types.js";

const STORE_DIR = join(homedir(), ".local", "share", "pi-router");
const CONFIG_PATH = join(STORE_DIR, "config.json");

const DEFAULT_CONFIG: RouterConfig = {
	verbosity: "debug", // install default per design decision #4
	defaultModel: "fireworks/accounts/fireworks/models/kimi-k2p6",
	offlineCacheSecs: 30,
};

export function loadConfig(): RouterConfig {
	if (!existsSync(CONFIG_PATH)) return { ...DEFAULT_CONFIG };
	try {
		const data = JSON.parse(readFileSync(CONFIG_PATH, "utf8")) as Partial<RouterConfig>;
		return { ...DEFAULT_CONFIG, ...data };
	} catch {
		return { ...DEFAULT_CONFIG };
	}
}

export function saveConfig(cfg: RouterConfig): void {
	mkdirSync(STORE_DIR, { recursive: true });
	writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2) + "\n", "utf8");
}

export function configPath(): string {
	return CONFIG_PATH;
}
