/**
 * Passive offline detection.
 *
 * We don't pre-flight ping anything — too much latency tax. Instead, when a
 * cloud call fails with a network signature, we mark "offline" for a short
 * window. While offline, the router restricts the catalog to local providers
 * (mlx, ollama).
 */

const OFFLINE_WINDOW_MS = 30_000;

let offlineUntil = 0;

const NETWORK_ERROR_PATTERNS = [
	/ENOTFOUND/i,
	/ECONNREFUSED/i,
	/ETIMEDOUT/i,
	/ENETUNREACH/i,
	/EHOSTUNREACH/i,
	/network/i,
	/fetch failed/i,
	/socket hang up/i,
];

export function isOnline(): boolean {
	return Date.now() >= offlineUntil;
}

export function markOffline(reason?: string): void {
	offlineUntil = Date.now() + OFFLINE_WINDOW_MS;
}

export function maybeMarkOfflineFromError(err: unknown): boolean {
	const msg = err instanceof Error ? err.message : String(err);
	if (NETWORK_ERROR_PATTERNS.some((rx) => rx.test(msg))) {
		markOffline(msg);
		return true;
	}
	return false;
}

export function clearOffline(): void {
	offlineUntil = 0;
}

export function offlineRemainingMs(): number {
	return Math.max(0, offlineUntil - Date.now());
}

/**
 * Local provider names. When offline, only these are eligible candidates.
 */
export const LOCAL_PROVIDERS = new Set(["mlx", "ollama"]);

export function isLocalProvider(provider: string): boolean {
	return LOCAL_PROVIDERS.has(provider);
}
