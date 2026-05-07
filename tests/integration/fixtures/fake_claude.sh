#!/usr/bin/env bash
# Deterministic fake `claude` CLI for the localhost e2e integration test (#34).
#
# Reads the prompt from stdin (until EOF or a single line, depending on FAKE_CLAUDE_MODE)
# and writes a fixed multi-token response on stdout, one token per line, with small
# sleeps between tokens to exercise the streaming path. Emits a turn-end sentinel
# before exiting cleanly with exit code 0.
#
# This script is intentionally NewLine-Delimited-JSON-shaped so it can stand in for
# the real `claude --output-format stream-json` interface without taking a hard dep
# on the real CLI's exact wire format. The server-side claude driver (#22) is
# expected to parse one JSON object per line.
#
# Environment:
#   FAKE_CLAUDE_TOKEN_DELAY_MS   — sleep between tokens (default 10ms)
#   FAKE_CLAUDE_RESPONSE         — override the fixed response phrase
#   FAKE_CLAUDE_MODE             — "line" (read one line) or "eof" (read until EOF)
#                                  default "line"
set -euo pipefail

delay_ms="${FAKE_CLAUDE_TOKEN_DELAY_MS:-10}"
mode="${FAKE_CLAUDE_MODE:-line}"
# A unique, unusual response phrase. Tests assert this appears on the client's
# stdout, and assert this phrase does NOT appear in plaintext on the wire.
response="${FAKE_CLAUDE_RESPONSE:-zephyr-quokka-prism-mango-vortex-glissando}"

# Read prompt. We don't actually use the prompt content for the deterministic
# echo; the integration test only cares that some response is streamed back.
if [[ "$mode" == "eof" ]]; then
  prompt="$(cat)"
else
  IFS= read -r prompt || true
fi

# Convert delay from ms to seconds for `sleep`.
delay_s=$(awk -v ms="$delay_ms" 'BEGIN { printf "%.3f", ms / 1000 }')

# Stream tokens, one JSON object per line. Format:
#   {"type":"assistant_token","text":"<token>"}
# followed by {"type":"assistant_turn_end"}.
# The hyphen-separated phrase becomes individual tokens.
IFS='-' read -r -a tokens <<<"$response"
for tok in "${tokens[@]}"; do
  printf '{"type":"assistant_token","text":"%s"}\n' "$tok"
  sleep "$delay_s"
done
printf '{"type":"assistant_turn_end"}\n'
exit 0
