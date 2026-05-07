#!/usr/bin/env bash
# tabula GPU VM bootstrap — issue #35.
#
# Runs on every boot of the GPU VM (driven from cloud-init.yaml). On first
# boot it installs the claude CLI, clones the substrate from the in-enclave
# Gitea, and writes the tabula-agent systemd unit. On subsequent boots it
# is a no-op except for `git pull` and (re)starting the systemd unit.
#
# Configuration is read entirely from the GCE metadata service so this
# script ships verbatim — no Terraform templating in the file body, which
# keeps shellcheck happy and the trust surface small.
#
# Required instance attributes (set by the Terraform GPU module):
#   tabula-enclave-name       — short enclave name (lowercase)
#   tabula-vertex-project     — GCP project hosting Vertex AI
#   tabula-vertex-region      — Vertex AI region (e.g. us-east5)
#   tabula-gitea-url          — base URL, e.g. https://gitea.<enclave>.internal:3000
#   tabula-gitea-repo         — owner/repo path, e.g. tabula/tabula
#   tabula-gitea-token-secret — Secret Manager resource id for the Gitea PAT
#   tabula-claude-version     — pinned claude CLI version, e.g. 1.0.45
#   tabula-git-user-email     — service identity for git user.email
#   tabula-agent-driver-sock  — path to the agent driver UDS, e.g. /run/tabula/agent.sock
#
# Trust boundary notes:
#   - Stock Ubuntu LTS image; everything tabula-specific lives here.
#   - VM only ever pulls code from in-enclave Gitea (#21) and only ever talks
#     to Vertex AI via the PSC endpoint (#23). It MUST NOT reach GitHub or
#     the public Anthropic API. The egress allowlist on the network module
#     (#14) is the enforcement point; this script just respects it.
#   - GPU service account (#15) holds:
#       * roles/aiplatform.user                — call Vertex AI
#       * roles/secretmanager.secretAccessor   — fetch the Gitea token (scoped)
#     No JSON key files are baked in. ADC comes from the metadata server.
#
# All output is tee'd to /var/log/tabula-bootstrap.log. The Cloud Logging
# agent (audit issue #38) sinks that file separately.

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Static paths.
# ---------------------------------------------------------------------------

WORK_DIR="/opt/tabula/work"
STATE_DIR="/opt/tabula"
LOG_FILE="/var/log/tabula-bootstrap.log"
SENTINEL_BOOTSTRAPPED="${STATE_DIR}/.bootstrapped"
SENTINEL_VERSION="${STATE_DIR}/.claude-version"
SYSTEMD_UNIT="/etc/systemd/system/tabula-agent.service"
GIT_CREDS_FILE="/etc/tabula/git-credentials"

METADATA_BASE="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
METADATA_HEADER="Metadata-Flavor: Google"

# ---------------------------------------------------------------------------
# Logging.
# ---------------------------------------------------------------------------

mkdir -p "${STATE_DIR}" "$(dirname "${LOG_FILE}")"
# Send every line of stdout/stderr to both the journal (cloud-init capture)
# and our dedicated log file. `tee -a` is intentional so re-runs accumulate.
exec > >(tee -a "${LOG_FILE}") 2>&1

log() {
  printf '[tabula-bootstrap] %s %s\n' "$(date -u +%FT%TZ)" "$*"
}

trap 'log "FAILED at line ${LINENO}"' ERR

# ---------------------------------------------------------------------------
# Metadata accessors.
# ---------------------------------------------------------------------------

# Fetch a required metadata attribute. Exits non-zero if missing or empty.
metadata_required() {
  local key="$1"
  local value
  value="$(curl -fsS -H "${METADATA_HEADER}" "${METADATA_BASE}/${key}" || true)"
  if [[ -z "${value}" ]]; then
    log "ERROR: required metadata attribute '${key}' is missing or empty"
    return 1
  fi
  printf '%s' "${value}"
}

ENCLAVE_NAME="$(metadata_required tabula-enclave-name)"
VERTEX_PROJECT_ID="$(metadata_required tabula-vertex-project)"
VERTEX_REGION="$(metadata_required tabula-vertex-region)"
GITEA_URL="$(metadata_required tabula-gitea-url)"
GITEA_REPO_PATH="$(metadata_required tabula-gitea-repo)"
GITEA_TOKEN_SECRET="$(metadata_required tabula-gitea-token-secret)"
CLAUDE_VERSION="$(metadata_required tabula-claude-version)"
GIT_USER_EMAIL="$(metadata_required tabula-git-user-email)"
AGENT_DRIVER_SOCKET="$(metadata_required tabula-agent-driver-sock)"

log "begin (enclave=${ENCLAVE_NAME}, claude=${CLAUDE_VERSION})"

# ---------------------------------------------------------------------------
# Phase A — package install (first boot only path; idempotent regardless).
# ---------------------------------------------------------------------------

install_packages() {
  log "apt update + install (git, curl, ca-certificates, jq, socat)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    jq \
    socat
}

# ---------------------------------------------------------------------------
# Phase B — claude CLI install.
#
# Anthropic ships an official installer. To keep the trust surface minimal,
# we pin the version via the tabula-claude-version metadata attribute and
# verify the installed binary reports that exact version.
#
# NOTE: as of 2025-Q4 the installer is `https://claude.ai/install.sh`. Confirm
# at apply time. If a pinned tarball is preferred, swap this function for a
# direct `curl -O` of the tarball into /usr/local/bin and a `sha256sum -c`.
# ---------------------------------------------------------------------------

install_claude() {
  if [[ -f "${SENTINEL_VERSION}" ]] \
     && [[ "$(cat "${SENTINEL_VERSION}")" == "${CLAUDE_VERSION}" ]] \
     && command -v claude >/dev/null 2>&1; then
    log "claude ${CLAUDE_VERSION} already installed; skipping"
    return 0
  fi

  log "installing claude CLI version ${CLAUDE_VERSION}"
  # The official installer respects CLAUDE_INSTALL_VERSION when set.
  curl -fsSL https://claude.ai/install.sh \
    | CLAUDE_INSTALL_VERSION="${CLAUDE_VERSION}" bash -s -- --quiet

  # The installer drops the binary in /usr/local/bin or ~/.local/bin depending
  # on permissions. Resolve and symlink into a canonical path so systemd has a
  # stable ExecStart target.
  local resolved
  resolved="$(command -v claude || true)"
  if [[ -z "${resolved}" ]]; then
    log "ERROR: claude CLI not on PATH after install"
    return 1
  fi
  ln -sf "${resolved}" /usr/local/bin/claude

  # Confirm the version matches what we asked for. Exit non-zero on drift so
  # the systemd unit never starts against an unexpected build.
  local installed
  installed="$(/usr/local/bin/claude --version 2>&1 | awk '{print $1}')"
  if [[ "${installed}" != *"${CLAUDE_VERSION}"* ]]; then
    log "ERROR: claude reports '${installed}', expected '${CLAUDE_VERSION}'"
    return 1
  fi

  printf '%s\n' "${CLAUDE_VERSION}" > "${SENTINEL_VERSION}"
  log "claude ${CLAUDE_VERSION} installed at $(readlink -f /usr/local/bin/claude)"
}

# ---------------------------------------------------------------------------
# Phase C — Vertex ADC smoke test.
#
# The attached service account's token is what `claude` will use to call
# Vertex AI. We exercise the metadata-server ADC path explicitly so any auth
# misconfiguration shows up in this log line instead of as a confusing model
# error later. We deliberately do NOT cache the access token — we only verify
# it can be minted.
# ---------------------------------------------------------------------------

smoke_test_adc() {
  log "verifying Vertex ADC via gcloud auth application-default print-access-token"
  if ! gcloud auth application-default print-access-token --quiet >/dev/null; then
    log "ERROR: ADC token mint failed; is the GPU SA attached and does it have aiplatform.user?"
    return 1
  fi
  log "ADC ok"
}

# ---------------------------------------------------------------------------
# Phase D — git identity + Gitea credentials.
#
# The Gitea token is fetched from Secret Manager via the GPU SA. We write it
# into a git credential helper that is bound to the in-enclave Gitea host
# only, so it cannot leak to any other remote.
# ---------------------------------------------------------------------------

configure_git_identity() {
  git config --system user.email "${GIT_USER_EMAIL}"
  git config --system user.name "tabula-enclave-${ENCLAVE_NAME}"
}

write_git_credentials() {
  local token
  token="$(gcloud secrets versions access latest \
    --secret="${GITEA_TOKEN_SECRET}" --quiet 2>/dev/null || true)"
  if [[ -z "${token}" ]]; then
    log "ERROR: empty Gitea token from Secret Manager (${GITEA_TOKEN_SECRET})"
    return 1
  fi

  install -d -m 0700 -o root -g root /etc/tabula
  # Build the credential URL. The 'gitea-bootstrap' username is conventional;
  # Gitea PATs ignore the username field but git's store helper requires one.
  local cred_url="${GITEA_URL/https:\/\//https://gitea-bootstrap:${token}@}"
  ( umask 077 && printf '%s\n' "${cred_url}" > "${GIT_CREDS_FILE}" )

  git config --system credential.helper "store --file=${GIT_CREDS_FILE}"
}

clone_or_update_repo() {
  local remote="${GITEA_URL}/${GITEA_REPO_PATH}.git"
  if [[ -d "${WORK_DIR}/.git" ]]; then
    log "git pull in ${WORK_DIR}"
    git -C "${WORK_DIR}" remote set-url origin "${remote}"
    git -C "${WORK_DIR}" fetch --prune origin
    git -C "${WORK_DIR}" reset --hard origin/HEAD
  else
    log "git clone ${remote} -> ${WORK_DIR}"
    install -d -m 0755 "$(dirname "${WORK_DIR}")"
    git clone --depth 1 "${remote}" "${WORK_DIR}"
  fi
}

# ---------------------------------------------------------------------------
# Phase E — systemd unit for the long-lived agent.
#
# The unit launches `claude` in stream-json mode. The agent driver from #22
# wraps stdin/stdout — for MVP we point it at a Unix domain socket that the
# driver listens on. The `socat` invocation bridges the socket to the claude
# process; replace with the driver binary once #22 lands.
#
# Vertex env vars (verify against current Anthropic docs at apply time —
# they have evolved; the names below are accurate as of 2025-Q4):
#   CLAUDE_CODE_USE_VERTEX=1               — switch backend from anthropic.com
#   ANTHROPIC_VERTEX_PROJECT_ID=<project>  — GCP project hosting Vertex
#   CLOUD_ML_REGION=<region>               — Vertex AI region
# We deliberately leave GOOGLE_APPLICATION_CREDENTIALS unset so the SDK falls
# back to metadata-server ADC.
# ---------------------------------------------------------------------------

write_systemd_unit() {
  log "writing ${SYSTEMD_UNIT}"
  install -d -m 0755 "$(dirname "${AGENT_DRIVER_SOCKET}")"

  cat > "${SYSTEMD_UNIT}" <<UNIT
[Unit]
Description=Tabula agent (claude CLI in stream-json mode, Vertex AI backend)
After=network-online.target
Wants=network-online.target
ConditionPathExists=${WORK_DIR}

[Service]
Type=simple
WorkingDirectory=${WORK_DIR}
Environment=CLAUDE_CODE_USE_VERTEX=1
Environment=ANTHROPIC_VERTEX_PROJECT_ID=${VERTEX_PROJECT_ID}
Environment=CLOUD_ML_REGION=${VERTEX_REGION}
# Driver socket — issue #22 owns the consumer side. socat bridges UDS <-> claude.
ExecStartPre=/bin/rm -f ${AGENT_DRIVER_SOCKET}
ExecStart=/usr/bin/socat UNIX-LISTEN:${AGENT_DRIVER_SOCKET},fork,mode=600 EXEC:'/usr/local/bin/claude --input-format stream-json --output-format stream-json',pty,stderr
Restart=on-failure
RestartSec=2s
# Hardening — the GPU VM only runs this one workload.
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=${WORK_DIR} /run/tabula /var/log
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT

  systemctl daemon-reload
  systemctl enable tabula-agent.service
  systemctl restart tabula-agent.service
}

# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

main() {
  if [[ ! -f "${SENTINEL_BOOTSTRAPPED}" ]]; then
    log "first boot — running full bootstrap"
    install_packages
    install_claude
    configure_git_identity
    write_git_credentials
    clone_or_update_repo
    smoke_test_adc
    write_systemd_unit
    touch "${SENTINEL_BOOTSTRAPPED}"
    log "bootstrap complete"
  else
    log "wake — fast path"
    # Re-verify ADC (cheap), refresh the clone, ensure the unit is up.
    smoke_test_adc
    # Re-write credentials in case the secret was rotated since last boot.
    write_git_credentials
    clone_or_update_repo
    systemctl daemon-reload
    systemctl restart tabula-agent.service
    log "wake complete"
  fi
}

main "$@"
