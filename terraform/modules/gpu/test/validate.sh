#!/usr/bin/env bash
# Local validation harness for the GPU bootstrap (issue #35).
#
# What it does:
#   1. shellcheck on the canonical bootstrap.sh
#   2. Render the cloud-init body via test/render-cloud-init.py
#   3. shellcheck on the bootstrap.sh _as embedded inside_ the cloud-init
#   4. Structural YAML sanity (PyYAML)
#   5. (Linux only) `cloud-init devel schema --config-file ...`
#
# Exits non-zero on the first failure. Safe to run repeatedly.

set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE="$(dirname "${HERE}")"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

step() { printf '\n=== %s ===\n' "$*"; }

step "1. shellcheck bootstrap.sh (source of truth)"
shellcheck "${MODULE}/bootstrap.sh"

step "2. render cloud-init body"
python3 "${HERE}/render-cloud-init.py" > "${TMPDIR}/cloud-init.yaml"
echo "rendered: ${TMPDIR}/cloud-init.yaml ($(wc -l < "${TMPDIR}/cloud-init.yaml") lines)"

step "3. extract embedded bootstrap.sh and shellcheck it"
python3 - "${TMPDIR}/cloud-init.yaml" "${TMPDIR}/bootstrap.embedded.sh" <<'PY'
import sys, yaml
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    data = yaml.safe_load(f)
script = next(
    f["content"] for f in data["write_files"]
    if f["path"] == "/opt/tabula/bootstrap.sh"
)
with open(dst, "w") as f:
    f.write(script)
PY
shellcheck "${TMPDIR}/bootstrap.embedded.sh"

step "4. structural cloud-config sanity (write_files + runcmd present)"
python3 - "${TMPDIR}/cloud-init.yaml" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
assert "write_files" in data, "missing write_files"
assert "runcmd" in data, "missing runcmd"
assert any(
    f["path"] == "/opt/tabula/bootstrap.sh" for f in data["write_files"]
), "bootstrap.sh not in write_files"
assert ["/opt/tabula/bootstrap.sh"] in data["runcmd"], (
    "bootstrap.sh not invoked by runcmd"
)
print("OK")
PY

step "5. cloud-init devel schema (Linux only; skipped if cloud-init not present)"
if command -v cloud-init >/dev/null 2>&1; then
  cloud-init devel schema --config-file "${TMPDIR}/cloud-init.yaml"
else
  echo "cloud-init not installed on this host — skipping schema check."
  echo "Run on a Linux box (or in CI) to satisfy the acceptance criterion."
fi

echo
echo "All checks passed."
