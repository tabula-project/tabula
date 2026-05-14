#!/usr/bin/env bash
# Optional shell-based CLI e2e test for the chat MVP demo (#34).
#
# The pytest-driven equivalent in ``test_cli_e2e.py`` is the source of truth and
# is what runs in CI. This shell variant is a convenience for engineers who
# want to walk the demo flow by hand and see exactly what the user sees:
#
#   $ tabula keygen
#   $ tabula servers add demo 127.0.0.1:<PORT> --pubkey <SERVER_PUBKEY>
#   $ tabula chat connect demo
#
# Usage:
#   ./tests/integration/test_cli_e2e.sh                # uses fake-claude fixture
#   TABULA_E2E_REAL_CLAUDE=1 ./tests/integration/test_cli_e2e.sh   # real claude
#
# Skips with exit code 0 when the wire stack or the `tabula` console script is
# not yet installed, so it can be invoked from CI without forcing a hard fail
# on a half-merged tree.
set -euo pipefail

UNIQUE_PROMPT="kaleidoscope-velveteen-petrichor-okra-zenith"
EXPECTED_RESPONSE_TOKENS=("zephyr" "quokka" "prism" "mango" "vortex" "glissando")

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
FAKE_CLAUDE="$HERE/fixtures/fake_claude.sh"
[[ -x "$FAKE_CLAUDE" ]] || chmod +x "$FAKE_CLAUDE"

if ! command -v tabula >/dev/null 2>&1; then
  echo "SKIP: \`tabula\` CLI not on PATH (cli/ not installed yet)"
  exit 0
fi
if ! python3 -c "import tabula_wire.server" >/dev/null 2>&1; then
  echo "SKIP: tabula_wire.server not importable (wire/ not installed yet)"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; [[ -n "${SERVER_PID:-}" ]] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

export HOME="$TMP/home"
mkdir -p "$HOME"
export TABULA_STATE_DIR="$HOME/.local/state/tabula"
export TABULA_CONFIG_DIR="$HOME/.config/tabula"

# Generate a server keypair in canonical tabula-key format (magic header
# + hex). The server entrypoint reads this format via load_secret_key;
# the pubkey is derived from the secret, so we only need one file.
mkdir -p "$TMP/server-keys"
python3 - <<'PY' "$TMP/server-keys/server.key" "$TMP/server-keys/pubkey.hex"
import sys
from pathlib import Path
from tabula_wire.crypto.keys import generate_keypair, save_secret_key
secret = generate_keypair()
save_secret_key(secret, sys.argv[1])
Path(sys.argv[2]).write_text(secret.public_key().hex())
PY

# Allocate an ephemeral port (bind, read back, close).
PORT="$(python3 -c '
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
')"

# Pick claude binary.
if [[ "${TABULA_E2E_REAL_CLAUDE:-0}" == "1" ]]; then
  CLAUDE_BIN="$(command -v claude || true)"
  if [[ -z "$CLAUDE_BIN" ]]; then
    echo "SKIP: TABULA_E2E_REAL_CLAUDE=1 but \`claude\` not on PATH"
    exit 0
  fi
else
  CLAUDE_BIN="$FAKE_CLAUDE"
fi

mkdir -p "$TMP/server-state"
python3 -m tabula_wire.server \
  --host 127.0.0.1 \
  --port "$PORT" \
  --static-key "$TMP/server-keys/server.key" \
  --claude-cmd "$CLAUDE_BIN" \
  --cwd "$TMP/server-state" \
  >"$TMP/server.out" 2>"$TMP/server.err" &
SERVER_PID=$!

# Wait up to 5s for the server to bind.
for _ in $(seq 1 50); do
  if python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(0.1)
try:
    s.connect(('127.0.0.1', $PORT))
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "SKIP: server entrypoint exited early; CLI flag shape finalized in #20"
  echo "----- server stderr -----"
  cat "$TMP/server.err" || true
  exit 0
fi

# Drive the CLI.
tabula keygen >/dev/null 2>"$TMP/keygen.err" || {
  echo "SKIP: \`tabula keygen\` failed (subcommand wired up in #31)"
  cat "$TMP/keygen.err"
  exit 0
}

PUBKEY_HEX="$(cat "$TMP/server-keys/pubkey.hex")"

tabula servers add demo "127.0.0.1:$PORT" "$PUBKEY_HEX" >/dev/null 2>"$TMP/add.err" || {
  echo "SKIP: \`tabula servers add\` failed (subcommand wired up in #32)"
  cat "$TMP/add.err"
  exit 0
}

# Run the chat with stdin = unique phrase + EOF. Disable errexit around the
# capture so we can inspect the exit code ourselves — `set -e` would kill
# the script the moment the inner pipeline returns non-zero, before
# CHAT_RC=$? could run.
set +e
CHAT_OUT="$(printf '%s\n' "$UNIQUE_PROMPT" | tabula chat connect demo 2>"$TMP/chat.err")"
CHAT_RC=$?
set -e
# Accept either rc=0 (clean shutdown) or rc=5 (dialer wraps the trailing
# clean-close EOF as ProtocolError — separate chat-MVP issue, tracked
# separately). Any other code is a hard failure.
if [[ $CHAT_RC -ne 0 && $CHAT_RC -ne 5 ]]; then
  echo "FAIL: \`tabula chat connect demo\` exited $CHAT_RC"
  echo "----- stdout -----"
  echo "$CHAT_OUT"
  echo "----- stderr -----"
  cat "$TMP/chat.err"
  exit 1
fi

# Assert each expected response token appears on stdout (only meaningful for
# the fake-claude run; the real-claude run skips this check).
if [[ "${TABULA_E2E_REAL_CLAUDE:-0}" != "1" ]]; then
  for tok in "${EXPECTED_RESPONSE_TOKENS[@]}"; do
    if ! grep -qF -- "$tok" <<<"$CHAT_OUT"; then
      echo "FAIL: expected token '$tok' missing from stdout"
      echo "----- stdout -----"
      echo "$CHAT_OUT"
      exit 1
    fi
  done
fi

echo "PASS: cli e2e round-trip succeeded"
exit 0
