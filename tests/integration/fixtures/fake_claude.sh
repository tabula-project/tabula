#!/usr/bin/env bash
# Deterministic fake `claude` CLI for the localhost e2e integration test (#34).
#
# Reads claude-shaped stream-json user messages from stdin (one JSON object
# per line, ``{"type": "user", "message": {"content": "..."}}``) and writes
# claude-shaped stream-json events to stdout — the subset that the canonical
# claude driver in ``tabula_wire.server.claude_driver`` parses:
#
#   - {"type": "system", "subtype": "init", ...}              announce
#   - {"type": "stream_event", "event": {
#         "type": "content_block_delta",
#         "delta": {"type": "text_delta", "text": "<token>"}}}
#   - {"type": "result", "subtype": "success"}                end-of-turn
#
# One turn is produced per stdin line. The response phrase is split on
# hyphens; each token becomes a separate ``content_block_delta`` so the
# wire path streams multiple frames per turn. The integration test asserts
# every token appears in the client's received stream AND that the phrase
# never appears in plaintext on the wire.
#
# Environment:
#   FAKE_CLAUDE_TOKEN_DELAY_MS  — sleep between tokens (default 10ms)
#   FAKE_CLAUDE_RESPONSE        — override the fixed response phrase

set -u

delay_ms="${FAKE_CLAUDE_TOKEN_DELAY_MS:-10}"
response="${FAKE_CLAUDE_RESPONSE:-zephyr-quokka-prism-mango-vortex-glissando}"
delay_s=$(awk -v ms="$delay_ms" 'BEGIN { printf "%.3f", ms / 1000 }')

emit() {
  python3 -c "import json,sys; print(json.dumps($1), flush=True)"
}

# Announce ourselves so the driver can sync on the session.
emit '{"type": "system", "subtype": "init", "session_id": "fake-integration"}'

# One turn per stdin line. Stream every response token, then end the turn.
IFS='-' read -r -a tokens <<<"$response"
while IFS= read -r _line; do
  for tok in "${tokens[@]}"; do
    emit "{'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '${tok}'}}}"
    sleep "$delay_s"
  done
  emit "{'type': 'result', 'subtype': 'success'}"
done
