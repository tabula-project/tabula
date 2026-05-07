/**
 * Per-provider throttle tracking.
 *
 * When a provider returns 429 (rate limit) or 5xx (server error), we mark
 * it "throttled" for a window. During the window, rules that would route
 * to that provider are skipped — the next rule fires instead.
 *
 * Cerebras Free tier is the most common offender (very low rate limits).
 */

const THROTTLE_WINDOW_MS = 5 * 60_000; // 5 minutes

const throttledUntil = new Map<string, number>();

export function isThrottled(provider: string): boolean {
	const t = throttledUntil.get(provider);
	if (!t) return false;
	if (Date.now() >= t) {
		throttledUntil.delete(provider);
		return false;
	}
	return true;
}

export function markThrottled(provider: string, durationMs?: number): void {
	throttledUntil.set(provider, Date.now() + (durationMs ?? THROTTLE_WINDOW_MS));
}

export function throttleRemainingMs(provider: string): number {
	const t = throttledUntil.get(provider);
	if (!t) return 0;
	return Math.max(0, t - Date.now());
}

export function clearThrottle(provider: string): void {
	throttledUntil.delete(provider);
}

const RATE_LIMIT_PATTERNS = [
	/\b429\b/,
	/rate.?limit/i,
	/quota.?exceed/i,
	/too.?many.?requests/i,
	/queue_exceeded/i,
	/queue.?full/i,
];

const SERVER_ERROR_PATTERNS = [
	/\b5\d{2}\b/, // 5xx HTTP
	/internal.?server.?error/i,
	/service.?unavailable/i,
	/bad.?gateway/i,
];

export function detectThrottleFromError(err: unknown): "rate-limit" | "server-error" | null {
	const msg = err instanceof Error ? err.message : String(err);
	if (RATE_LIMIT_PATTERNS.some((rx) => rx.test(msg))) return "rate-limit";
	if (SERVER_ERROR_PATTERNS.some((rx) => rx.test(msg))) return "server-error";
	return null;
}
