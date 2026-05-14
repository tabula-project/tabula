#!/usr/bin/env bash
# Fake `claude` CLI for unit testing wire/server/claude_driver.py.
#
# Behavior is selected by FAKE_CLAUDE_MODE (env). It reads stream-json user
# messages from stdin (one JSON object per line) and writes claude-shaped
# stream-json events to stdout (one JSON object per line), matching the
# subset that ClaudeProcess parses.
#
# Modes:
#
#   echo       (default) For each stdin line, emit a content_block_delta
#              with the input text echoed back, followed by a result event.
#              Exits 0 on stdin EOF.
#
#   multiturn  Same as echo, but tags each delta with a turn counter so
#              tests can assert turn-by-turn progress.
#
#   crash      Emit one delta on the first message, then write to stderr
#              and exit 7 mid-stream — never sends a result event.
#
#   sleep      Emit a system init event, then block forever (until killed).
#              Used to test ClaudeProcess.kill().
#
#   bad_json   Emit one line of garbage, one valid result event, exit 0.
#              Used to test the driver's tolerance of non-JSON lines.
#
#   dual       Emit BOTH stream_event content_block_delta partials AND a
#              final `assistant` whole-message event with the same content,
#              then a result event. Matches the behavior of real
#              `claude --include-partial-messages` and pins the driver's
#              de-duplication logic — without the dedup gate the chat client
#              receives every response twice (issue #100).
#
# This fixture is intentionally written in plain bash + a tiny python helper
# so it has no third-party dependencies.

set -u

mode="${FAKE_CLAUDE_MODE:-echo}"

# Helper: emit a JSON line. Args: a python-style dict literal.
emit() {
  python3 -c "import json,sys; print(json.dumps($1), flush=True)"
}

# Always announce ourselves first so tests can sync.
emit '{"type": "system", "subtype": "init", "session_id": "fake"}'

case "$mode" in
  sleep)
    # Block until killed. We trap signals so we exit non-zero on SIGTERM
    # (real `claude` would just exit; either is fine — driver only checks
    # that the process is gone).
    trap 'exit 143' TERM
    trap 'exit 130' INT
    # Read in background so we don't hold up the parent's stdin pipe; the
    # body of this case just sleeps.
    while sleep 1; do :; done
    ;;

  crash)
    # Read first prompt, emit a single delta, then die mid-stream.
    if IFS= read -r line; then
      emit '{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial..."}}}'
      echo "fake_claude: simulated crash" >&2
      exit 7
    fi
    exit 7
    ;;

  bad_json)
    # First line of garbage, then a valid turn, then exit.
    printf '%s\n' 'this is not json {{{'
    if IFS= read -r line; then
      emit '{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}}'
      emit '{"type": "result", "subtype": "success"}'
    fi
    ;;

  multiturn)
    turn=0
    while IFS= read -r line; do
      turn=$((turn + 1))
      content=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['message']['content'])" "$line")
      emit "{'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'turn${turn}:${content}'}}}"
      emit "{'type': 'result', 'subtype': 'success', 'turn': ${turn}}"
    done
    ;;

  dual)
    # Real `claude --include-partial-messages` emits both:
    #   - one or more stream_event content_block_delta partials
    #   - a final `assistant` event with the assembled whole message
    # The driver must yield each chunk exactly once. Two deltas + matching
    # assistant message is the minimum that exercises the dedup gate.
    while IFS= read -r line; do
      content=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['message']['content'])" "$line")
      emit "{'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'echo: '}}}"
      emit "{'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '${content}'}}}"
      emit "{'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'echo: ${content}'}]}}"
      emit "{'type': 'result', 'subtype': 'success'}"
    done
    ;;

  echo|*)
    while IFS= read -r line; do
      content=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['message']['content'])" "$line")
      # Two deltas + a completed assistant message form, exercising both
      # stream_event and assistant event paths in the driver.
      emit "{'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'echo: '}}}"
      emit "{'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '${content}'}}}"
      emit "{'type': 'result', 'subtype': 'success'}"
    done
    ;;
esac
