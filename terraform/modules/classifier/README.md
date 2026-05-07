# `terraform/modules/classifier`

Provisions the **classifier VM** for a Tabula enclave (parent epic: #12).

The classifier is the always-on, cheap-warm front door of the enclave. It:

- terminates the Noise transport (server code from Epic #13, deployed on top of this VM later),
- runs a small OSS classifier model under [Ollama](https://ollama.com) for haiku-class routing decisions, and
- signals the GPU VM to wake when a request needs the heavy agent.

This module ONLY creates the VM and bootstrap. IAM bindings, firewall rules,
wake-signal logic, and Noise terminator code live in their own modules / issues.

## Sizing

Default `machine_type` is **`e2-medium`** (2 vCPU / 4 GB RAM, ~\$25/month always-on
in `us-central1`). This gives comfortable headroom for the default model
(`llama3.2:1b`, ~1.3 GB resident) plus the Noise terminator and a tiny overhead.

`e2-small` (2 vCPU / 2 GB RAM, ~\$13/month) is supported and documented as the
cheaper option. It still fits the default model but leaves little headroom for
the Noise stack — pick `e2-small` only if you have measured the production
working set.

Boot disk defaults to **30 GB pd-balanced** which fits OS + one small Ollama
model with headroom for logs and a future swap. Both are configurable.

## Model choice

Default model is **`llama3.2:1b`** (Meta, Llama 3.2 1B-instruct, ~1.3 GB).
Rationale:

- Small enough for `e2-small` and quick to pull on first boot.
- Strong enough at instruction-following to act as a haiku-class router.
- Permissively licensed for commercial use under the Llama Community License.

Alternative documented for callers that need a tighter footprint:

- `qwen2.5:0.5b` (~400 MB) — even smaller, faster, weaker at routing edge cases.

### Swap procedure

1. Update the `model_name` variable on the calling root module.
2. `terraform apply` — Terraform will rewrite the GCE metadata (`user-data`).
3. The change in `user-data` triggers an instance replacement on next apply.
   If you instead want a hot swap without recreating the VM:
   - SSH into the VM via IAP.
   - `ollama pull <new-model>` to fetch.
   - Update the routing config (lives in the Noise terminator code, Epic #13)
     to point at the new tag.
   - Optionally `ollama rm <old-model>` once routing is confirmed.

## Network posture

- **No external IP.** The VM relies on Cloud NAT for egress and IAP for SSH.
- **Network tags** `enclave-workload` and `enclave-classifier` are applied
  unconditionally so the ingress-firewall module can target the VM. Additional
  tags can be passed via `network_tags` and are unioned in.
- The `subnet_id` must point at the enclave's private subnet from the VPC module.

### Required NAT egress allowlist

For the cloud-init bootstrap to succeed, the Cloud NAT egress allowlist must
permit:

- `ollama.com` (and CDN: `*.ollama.ai`) — for the Ollama install + model pull.
- The Debian package mirrors used by your base image (`deb.debian.org` and the
  GCP-side Debian mirror that Debian 12 cloud images point at by default).

These allowances must be coordinated with the VPC / NAT module sub-issue.

## Security posture

- **Shielded VM** enabled (Secure Boot, vTPM, integrity monitoring).
- **OS Login** enabled and **project-wide SSH keys blocked** — the only path
  in is IAP + OS Login.
- The hardened systemd unit for `ollama.service` enables `NoNewPrivileges`,
  `ProtectSystem=strict`, `ProtectHome`, and a tight `ReadWritePaths` list.
- The service account passed via `service_account_email` must already have
  least-privilege bindings; this module does **not** create or bind IAM
  policies. That responsibility lives in the IAM module.

### Residual trust on the Ollama installer

The bootstrap currently fetches `https://ollama.com/install.sh` and pipes it
into `sh`. This is the canonical install path documented by Ollama, and it
is **the residual trust boundary of this module**: a compromise of
`ollama.com` would compromise the classifier VM at first boot.

This trust is explicitly accepted for MVP. The audit-logging issue should
flag the install-script fetch as a notable event. Future work removes this
trust (see "Future work" below).

The install is sentinel-guarded (`/var/lib/tabula/ollama-installed`) so the
script is only fetched on the first boot of a given disk; subsequent boots
do not re-pull from `ollama.com`.

## First-boot lifecycle

The cloud-init payload writes:

| Path | Purpose |
|---|---|
| `/var/lib/tabula/` | Sentinel directory for idempotent first-boot steps. |
| `/etc/systemd/system/ollama.service` | Hardened systemd unit for Ollama. |
| `/etc/systemd/system/tabula-bootstrap.service` | One-shot service that runs the bootstrap script on every boot (idempotent). |
| `/opt/tabula/bootstrap.sh` | Idempotent installer + model puller. |
| `/opt/tabula/wake-gpu.sh` | Placeholder for the GPU wake-signal (separate issue). |

On every boot, `tabula-bootstrap.service` runs `bootstrap.sh`. The script is
sentinel-guarded so re-runs are cheap:

1. If `/var/lib/tabula/ollama-installed` is missing, install Ollama and write
   the sentinel.
2. Activate the hardened `ollama.service` unit.
3. Wait for the API to come up.
4. If `/var/lib/tabula/model-pulled.<model>` is missing, run `ollama pull
   <model>` and write the sentinel.

## `wake-gpu.sh` placeholder

The wake-signal mechanism is intentionally **not** implemented here — that's
a separate sub-issue of Epic #12. The placeholder lives at
`/opt/tabula/wake-gpu.sh`, exits 0, and logs a TODO line. Its existence
locks the path + exit-code contract so the future implementation drops in
without callers having to change.

The variables `gpu_instance_name` and `gpu_instance_zone` are passed through
as GCE metadata and embedded in the placeholder script so the future
implementation has a known target — no hand-wiring required.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `project_id` | `string` | — | GCP project ID. |
| `region` | `string` | — | GCP region (must match the VPC subnet). |
| `zone` | `string` | — | GCP zone within `region`. |
| `name` | `string` | `tabula-classifier` | Instance name. |
| `machine_type` | `string` | `e2-medium` | GCE machine type. `e2-small` is the cheaper option. |
| `disk_size_gb` | `number` | `30` | Boot disk size in GB. |
| `disk_type` | `string` | `pd-balanced` | Boot disk type. |
| `image_family` | `string` | `debian-12` | Boot image family — repo-wide standard. |
| `image_project` | `string` | `debian-cloud` | Boot image project. |
| `subnet_id` | `string` | — | VPC subnet self-link / ID. |
| `service_account_email` | `string` | — | Pre-provisioned classifier SA. |
| `service_account_scopes` | `list(string)` | `["…/cloud-platform"]` | OAuth scopes. |
| `model_name` | `string` | `llama3.2:1b` | Ollama model tag. |
| `gpu_instance_name` | `string` | `""` | GPU VM name for wake-signal target. |
| `gpu_instance_zone` | `string` | `""` | GPU VM zone for wake-signal target. |
| `network_tags` | `list(string)` | `[]` | Extra tags (unioned with required tags). |
| `labels` | `map(string)` | see `variables.tf` | Resource labels. |
| `deletion_protection` | `bool` | `false` | Enclaves are disposable; off by default. |
| `metadata` | `map(string)` | `{}` | Extra GCE metadata; user wins on key collision. |

## Outputs

| Name | Description |
|---|---|
| `instance_name` | Name of the classifier instance. |
| `internal_ip` | Primary internal IP (no external IP exists). |
| `zone` | Zone the instance was created in. |
| `self_link` | GCE self-link for the instance. |
| `network_tags` | All applied tags (firewall rules can target these). |
| `service_account_email` | SA passthrough for downstream modules. |

## Example usage

```hcl
module "classifier" {
  source = "../../modules/classifier"

  project_id = var.project_id
  region     = "us-central1"
  zone       = "us-central1-a"

  subnet_id             = module.network.private_subnet_self_link
  service_account_email = module.iam.classifier_sa_email

  # Pass the GPU VM coordinates so the wake-signal placeholder is wired up.
  # Both can be empty strings before the GPU VM exists.
  gpu_instance_name = module.gpu.instance_name
  gpu_instance_zone = module.gpu.zone
}
```

## Future work (out of scope here)

- **Bake a custom GCE image** with Ollama pre-installed. Faster cold boot and
  removes the residual `ollama.com` trust at boot.
- **Mirror the install script + binary into a private GCS bucket** inside the
  enclave. Cheaper alternative to a custom image while still removing the
  external trust.
- **Replace the `wake-gpu.sh` placeholder** with the real implementation in
  the wake-signal sub-issue.
- **Audit logging** of the bootstrap (separate sub-issue) should flag the
  Ollama installer fetch as a notable event.

## Validation

This module ships with a fixture-free shape: no `terraform plan` is invoked
inside the module itself. To validate locally:

```sh
cd terraform/modules/classifier
terraform init -backend=false
terraform validate
terraform fmt -check -recursive
```

A `plan` against a real VPC + IAM module fixture is the integration test for
the parent enclave root module (separate sub-issue of Epic #12).
