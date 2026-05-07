# GPU VM bootstrap (issue #35)

This document covers the runtime configuration of the GPU VM — the `claude`
CLI install, the in-enclave Gitea clone, the Vertex AI wiring, and the
`tabula-agent.service` systemd unit. The infrastructure that hosts this is
owned by the GPU module's `main.tf` (issue [#19]), and once both land it
should be folded into the main module README.

[#19]: https://github.com/tabula-project/tabula/issues/19

## Files

| File                       | Owner                       | Purpose                                                                 |
| -------------------------- | --------------------------- | ----------------------------------------------------------------------- |
| `bootstrap.sh`             | this issue (#35)            | Plain-bash, idempotent boot script. Reads config from instance metadata.|
| `cloud-init.yaml.tftpl`    | this issue (#35)            | cloud-config template; embeds `bootstrap.sh` and runs it once per boot. |
| `bootstrap.tf`             | this issue (#35)            | Terraform variables + locals that render the cloud-init body.           |
| `test/render-cloud-init.py`| this issue (#35)            | Local dev aid: renders the cloud-init for `cloud-init devel schema`.    |

## How the pieces fit

```
    Terraform apply
         │
         ▼
   bootstrap.tf                 cloud-init.yaml.tftpl
   - reads bootstrap.sh   ────► - templatefile() embeds bootstrap.sh
   - exposes locals.user_data        as the body of /opt/tabula/bootstrap.sh
   - exposes locals.metadata          via write_files
         │
         ▼
   main.tf (issue #19)
   metadata = merge(
     local.tabula_bootstrap_metadata,        # tabula-* attrs that
     { user-data = local.tabula_bootstrap_user_data }   # bootstrap.sh reads
   )
         │
         ▼
   GCE instance boot
   1. cloud-init drops bootstrap.sh into /opt/tabula/bootstrap.sh
   2. cloud-init runcmd executes /opt/tabula/bootstrap.sh
   3. bootstrap.sh reads tabula-* metadata attrs, installs claude,
      clones the substrate from in-enclave Gitea, writes
      /etc/systemd/system/tabula-agent.service, and starts it
```

## Inputs (variables added by `bootstrap.tf`)

| Variable             | Required | Description                                                                                  |
| -------------------- | -------- | -------------------------------------------------------------------------------------------- |
| `vertex_project_id`  | yes      | GCP project hosting Vertex AI; passed to claude as `ANTHROPIC_VERTEX_PROJECT_ID`.            |
| `vertex_region`      | yes      | Vertex AI region; passed as `CLOUD_ML_REGION`. Must match the PSC endpoint region (#23).     |
| `gitea_url`          | yes      | Base URL of in-enclave Gitea, e.g. `https://gitea.<enclave>.internal:3000`.                  |
| `gitea_repo_path`    | yes      | `<owner>/<repo>` path inside Gitea (no leading slash, no `.git`).                            |
| `gitea_token_secret` | yes      | Secret Manager short name of the Gitea PAT. GPU SA must have access scoped to this secret.  |
| `claude_version`     | yes      | Pinned claude CLI version. Bootstrap refuses to start the agent on version drift.            |
| `git_user_email`     | yes      | Service identity for `git config user.email`.                                                |
| `agent_driver_socket`| no       | UDS path for the agent driver (#22). Default `/run/tabula/agent.sock`.                       |

## Wiring into the GPU instance (one-line change in `main.tf`)

Once both this issue and #19 are merged, the `google_compute_instance.gpu`
resource in `main.tf` should be updated to:

```hcl
metadata = merge(
  local.tabula_bootstrap_metadata,
  {
    enable-oslogin = "TRUE"
    user-data      = local.tabula_bootstrap_user_data
  },
)
# Drop the metadata_startup_script line — cloud-init drives the bootstrap.
```

## Idempotency contract

`bootstrap.sh` divides each boot into one of two paths, gated by the sentinel
file `/opt/tabula/.bootstrapped`:

- **First boot** — installs apt packages, installs the pinned claude CLI,
  configures git, fetches the Gitea token from Secret Manager, clones the
  substrate, smoke-tests Vertex ADC, writes the systemd unit, starts it.
- **Wake** — verifies Vertex ADC, refreshes the Gitea credential file (in
  case the secret rotated), `git fetch + reset --hard origin/HEAD`, and
  restarts the systemd unit.

The claude version is independently sentinelled (`/opt/tabula/.claude-version`)
so a `tabula-claude-version` metadata bump triggers re-install on the next
boot without having to delete the main sentinel. **Re-running the entire
script multiple times in sequence is safe**: each phase is gated and
re-entrant.

## Vertex AI environment variables

The systemd unit launches `claude` with these env vars (verify against
current Anthropic Vertex docs at apply time — they have evolved; the names
below are accurate as of 2025-Q4):

| Variable                    | Set to                                | Notes                                                          |
| --------------------------- | ------------------------------------- | -------------------------------------------------------------- |
| `CLAUDE_CODE_USE_VERTEX`    | `1`                                   | Switch the CLI from `api.anthropic.com` to Vertex AI.          |
| `ANTHROPIC_VERTEX_PROJECT_ID` | `var.vertex_project_id`             | GCP project hosting Vertex.                                    |
| `CLOUD_ML_REGION`           | `var.vertex_region`                   | Vertex region; must match the PSC endpoint region (#23).       |
| *(unset)* `GOOGLE_APPLICATION_CREDENTIALS` | n/a                    | Force the SDK to use metadata-server ADC. No key files on disk.|

## Failure modes (and how to triage)

| Symptom                                                                 | Probable cause                                                       | Where to look                                                    |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `gcloud auth application-default print-access-token` exits non-zero     | GPU SA not attached, or missing `roles/aiplatform.user` (IAM #15)    | `journalctl -u google-osconfig-agent`; `gcloud auth list` on VM  |
| `claude` 401/403 against Vertex                                         | PSC endpoint (#23) misrouted, or wrong region                        | `dig vertex-ai.<region>...`, route table, `CLOUD_ML_REGION`      |
| Bootstrap aborts at `git clone`                                         | Gitea unreachable on the enclave VPC, or wrong `gitea_url`           | `curl -v $GITEA_URL` from VM; check firewall (#14) east-west rules |
| Bootstrap aborts at "empty Gitea token from Secret Manager"             | GPU SA missing `roles/secretmanager.secretAccessor`, or wrong secret | `gcloud secrets versions access latest --secret=<name>` as GPU SA |
| `claude` install fails with network error                               | Egress allowlist (#14 Cloud NAT) blocking `claude.ai`                | `/var/log/tabula-bootstrap.log`; `curl -fsSL https://claude.ai/install.sh` |
| `tabula-agent.service` keeps restarting                                 | claude version drift, or stream-json handshake mismatch with #22     | `systemctl status tabula-agent`; `journalctl -u tabula-agent`     |

## Smoke tests

These are run via SSH (or IAP — see #33) after `terraform apply` on a fresh
GPU VM. They exercise each phase of the bootstrap independently.

```bash
# 0. Get a shell on the GPU VM (after IAP and #33 land):
gcloud compute ssh "${ENCLAVE_NAME}-gpu" --tunnel-through-iap

# 1. Bootstrap log shows a "bootstrap complete" or "wake complete" line:
sudo tail -n 50 /var/log/tabula-bootstrap.log

# 2. Pinned claude version is installed:
claude --version
cat /opt/tabula/.claude-version

# 3. Vertex ADC is mintable from this host:
gcloud auth application-default print-access-token | head -c 30 && echo "...OK"

# 4. claude can reach Vertex with a one-shot prompt (uses CLAUDE_CODE_USE_VERTEX
#    env from the systemd unit; for an ad-hoc test, export them explicitly):
sudo systemctl show tabula-agent.service --property=Environment
CLAUDE_CODE_USE_VERTEX=1 \
  ANTHROPIC_VERTEX_PROJECT_ID="$(curl -fsS -H 'Metadata-Flavor: Google' \
      http://metadata.google.internal/computeMetadata/v1/instance/attributes/tabula-vertex-project)" \
  CLOUD_ML_REGION="$(curl -fsS -H 'Metadata-Flavor: Google' \
      http://metadata.google.internal/computeMetadata/v1/instance/attributes/tabula-vertex-region)" \
  claude --print 'reply with the literal word: OK'

# 5. systemd unit is active and the driver socket exists:
systemctl is-active tabula-agent.service
test -S /run/tabula/agent.sock && echo "socket present"

# 6. Round-trip a stream-json line through the driver socket:
echo '{"type":"user","message":{"role":"user","content":"reply OK"}}' \
  | sudo socat - "UNIX-CONNECT:/run/tabula/agent.sock" \
  | head -n 5
```

## Local validation (CI / pre-PR)

Run from `terraform/modules/gpu/`:

```bash
# Lint the bootstrap script (the source of truth):
shellcheck bootstrap.sh

# Render the cloud-init body the way Terraform will and lint the embedded
# bootstrap script as it appears inside the cloud-config:
python3 test/render-cloud-init.py > /tmp/cloud-init.rendered.yaml

# YAML syntax + structural sanity (works on any Python 3.x with PyYAML):
python3 -c "
import yaml
d = yaml.safe_load(open('/tmp/cloud-init.rendered.yaml'))
assert 'write_files' in d and 'runcmd' in d
script = next(f['content'] for f in d['write_files'] if f['path'] == '/opt/tabula/bootstrap.sh')
assert script.startswith('#!/usr/bin/env bash')
print('cloud-init structurally OK')
"

# cloud-init schema validation (Linux only — cloud-init isn't packaged for
# macOS or Windows). Run in CI or in a Linux VM:
cloud-init devel schema --config-file /tmp/cloud-init.rendered.yaml
```

## Scope boundaries

This issue strictly does NOT own:

- Wake-signal mechanism (#37)
- Noise terminator on the classifier (epic #13)
- The agent driver / stream-json wrapper itself (#22) — this issue only wires
  it into systemd via a placeholder `socat`-based bridge that #22 will
  replace.
- The PSC endpoint provisioning (#23) — this issue only consumes it via the
  Vertex env vars.
- Multi-repo clones, workspace management, GPU driver tuning, or key-file
  Vertex auth.

## Open questions for #22 integration

1. Should the driver listen on the Unix domain socket (current placeholder)
   or on a loopback TCP port? UDS is simpler; TCP on `127.0.0.1` is what the
   #22 stub currently uses. Resolve when #22 lands.
2. Does the driver want one long-lived `claude` process per agent socket
   connection, or a process pool? Current placeholder is one `claude`
   process per accepted UDS connection (`socat ... fork`).
