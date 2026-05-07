# Wire error semantics

This document describes the error vocabulary of the Tabula chat wire and
the behavior contract for both peers when each error occurs. It pairs
with `tabula_wire.errors` (the typed exception hierarchy) and
`tabula.wire.v1.ErrorFrame` (the wire representation).

Source of truth: issue #36.

## Layering

Two layers of vocabulary describe failure:

1. **Typed exceptions** in `tabula_wire.errors` — used *internally* by
   each peer's session loop to communicate failure between layers.
2. **`ErrorFrame`** (from `tabula.wire.v1`) — the *wire* representation.
   The server emits it; the client receives it and re-raises a typed
   exception in the CLI process.

The mapping table lives in `tabula_wire.errors.exception_to_error_code`.
Walk the input exception's MRO, return the first matching code.

## Typed exceptions

| Class                | Family             | Carries                                                          | Wire code         |
|----------------------|--------------------|------------------------------------------------------------------|-------------------|
| `HandshakeTimeout`   | `HandshakeError`   | `timeout_s`, `peer`                                              | `PROTOCOL`        |
| `ServerKeyMismatch`  | `HandshakeError`   | `expected_key` (bytes), `actual_key` (bytes), `server`           | `PROTOCOL`        |
| `MalformedHandshake` | `HandshakeError`   | `reason`, `bytes_seen`, `peer`                                   | `PROTOCOL`        |
| `ServerDisconnected` | `SessionError`     | `during`                                                         | `PROTOCOL`        |
| `ClientDisconnected` | `SessionError`     | `during`, `session_id`                                           | `PROTOCOL`        |
| `OversizeFrame`      | `SessionError`    | `advertised`, `limit`                                            | `PROTOCOL`        |
| `MalformedFrame`     | `SessionError`     | `reason`, `frame_kind`                                           | `PROTOCOL`        |
| `ClaudeCrashed`      | `SubprocessError` | `returncode`, `stderr_tail`                                      | `CLAUDE_CRASHED`  |
| `ClaudeTimeout`      | `SubprocessError` | `idle_timeout_s`                                                 | `CLAUDE_TIMEOUT`  |
| `AtCapacity`         | (top-level)        | `current`, `limit`                                               | `AT_CAPACITY`     |
| `InternalError`      | (top-level)        | (catch-all; preserves `__cause__`)                               | `INTERNAL`        |

## Wire codes

### `PROTOCOL` (exit code 2)

**When emitted:** any wire-level violation by either peer — bad
handshake, oversize frame, frame that decrypts but fails proto parsing,
mid-session disconnect, handshake timeout, server key pin mismatch.

**What the client should do:** print a one-line message to stderr,
exit 2. No retry.

**Suggested user-facing message:**

> "Tabula wire protocol error: \<exception message\>. The server may be
> running an incompatible version, or the connection was disturbed."

For `ServerKeyMismatch` specifically, also print both the expected and
actual public keys in hex so the operator can manually reconcile their
`~/.config/tabula/known_servers` pin (see #32).

### `CLAUDE_CRASHED` (exit code 3)

**When emitted:** the server's `claude` subprocess exited non-zero
during a turn. The session is torn down; no recovery.

**What the client should do:** print message to stderr, exit 3.

**Suggested user-facing message:**

> "The assistant subprocess crashed. Your last turn was lost. Try again;
> if this persists, check the enclave's logs."

### `CLAUDE_TIMEOUT` (exit code 4)

**When emitted:** the server's `claude` subprocess produced no stdout
for at least `idle_timeout_s` seconds. Default 60s. Configurable via
`TABULA_CLAUDE_IDLE_TIMEOUT_S` environment variable on the server.

The heuristic is **idle time**, not total elapsed time — long-thinking
responses still produce intermittent tokens, and capping total time
would unnecessarily kill legitimate long turns.

**What the client should do:** print message to stderr, exit 4.

**Suggested user-facing message:**

> "The assistant stopped responding for \<N\>s. The session has been
> closed. You can reconnect; consider raising
> `TABULA_CLAUDE_IDLE_TIMEOUT_S` if your prompts routinely take longer."

**Tradeoff:** raising the idle timeout reduces false positives on
slow/large prompts but delays detection of actual hangs. The default
60s assumes interactive `claude` usage; bump it for batch-style or very
long-context prompts.

### `AT_CAPACITY` (exit code 5)

**When emitted:** the server is at its configured concurrent-session
cap. The new session is refused before `Welcome` is sent. Capacity
limits live in #25.

**What the client should do:** print message to stderr, exit 5.

**Suggested user-facing message:**

> "The Tabula server is at capacity. Try again in a few moments."

### `INTERNAL` (exit code 6)

**When emitted:** any server-side exception not classified as one of
the above. The server wraps the original exception in `InternalError`
before translating, preserving the cause via `__cause__` so server
logs retain the actual stack trace. The wire message intentionally
does not echo arbitrary internals to the client.

**What the client should do:** print message to stderr, exit 6.

**Suggested user-facing message:**

> "Internal server error. Check the enclave's logs for details."

## Best-effort `ErrorFrame` on close

Per #36, the server attempts a final `ErrorFrame` write before closing
the Noise stream, but with a short (~500 ms) timeout that swallows any
exception. **Error reporting must never block teardown.** If the peer
already disconnected, the write will fail silently — the client will
see the TCP close and its frame loop will raise `ServerDisconnected`,
which is also `PROTOCOL` (exit 2).

## Mid-turn disconnect → subprocess kill

When the server's session loop catches `ClientDisconnected` mid-turn,
it invokes the subprocess kill mechanics owned by #22:

1. Send `SIGTERM` to the `claude` process.
2. After a short grace period (~2 s), send `SIGKILL` if still alive.
3. Remove the session row from the manager (#25).

#36 does not reimplement this logic — it only tests it from the
disconnect angle (one of the cases in `tests/integration/test_error_paths.py`).

## See also

- `wire/src/tabula_wire/errors.py` — the typed hierarchy and mapping table.
- `wire/proto/chat.proto` — the `ErrorFrame.Code` enum (owned by #16,
  augmented by this issue).
- Issue #36 — the originating audit issue.
- Epic #13 — the chat wire epic.
