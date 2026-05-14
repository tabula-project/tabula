# `terraform/enclave-prod/` — production composition

Real-GCP sibling to `terraform/enclave/`. This composition wires the real
modules under `terraform/modules/<name>/` and is what `tabula enclave up`
runs against when invoked with `--composition prod`.

## Why two compositions?

The default `terraform/enclave/` composition uses stub modules that make
**zero cloud API calls** — `terraform plan` and `apply` succeed against it
with no GCP credentials. That property is bought with one trade-off: applies
against the stub composition produce no real infrastructure (synthetic
outputs like `classifier_ip = 203.0.113.10`, which is RFC 5737 TEST-NET-3).

This composition trades the offline-plan property for real infrastructure.
Decision rationale: see issue #107 (architect proposal).

| Property | `terraform/enclave/` (stub) | `terraform/enclave-prod/` (this dir) |
|---|---|---|
| Offline `terraform plan` | ✅ Works without credentials | ❌ Requires ADC |
| Real GCP resources on apply | ❌ Synthetic outputs only | ✅ Creates VPC, VMs, IAM, firewall, PSC |
| Cost on apply | $0 | Real GCP bill (mostly T4 GPU when awake + gitea always-on) |
| CI test compatibility | ✅ Default; no secrets needed | ⚠️ Needs a GCP sandbox project |
| When to use | Default; offline dev; CI; smoke tests | Real dogfood; production enclaves |

## CLI integration

```bash
# Stub (default) — offline-plan-able
tabula enclave up smoke --dry-run

# Prod — requires `gcloud auth application-default login` first
tabula enclave up myenclave --composition prod --project my-gcp-project
```

The CLI's `--composition` flag picks the root module path. Both
compositions emit the same output names (`classifier_ip`, `noise_port`,
`enclave_name`) so the state file and `tabula enclave status` UI work
unchanged.

## Inputs

| Variable | Type | Default | Notes |
|---|---|---|---|
| `project_id` | string | required | GCP project; must have ADC creds for the user. |
| `region` | string | `us-central1` | Must offer T4 GPU quota. |
| `zone` | string | `us-central1-a` | Specific zone within region for VM placement. |
| `enclave_name` | string | required | DNS-safe, 3-30 chars. Same validation as stub composition. |
| `noise_port` | number | `7000` | Public TCP port for Noise XX. The classifier listens here. |

## Outputs

Matches the stub composition's output contract exactly:

| Output | Type | Source |
|---|---|---|
| `classifier_ip` | string | `module.classifier.internal_ip` (private; reach via IAP) |
| `noise_port` | number | `var.noise_port` (echo of input) |
| `enclave_name` | string | `var.enclave_name` (echo of input) |

## Module-to-module dependency graph

```
network -> firewall
network -> iam -> gpu
                \-> classifier
                \-> gitea
network -> classifier (subnet_id)
network -> gitea (subnet_self_link)
network -> gpu (subnet_self_link)
network -> vertex_psc
gpu -> classifier (gpu_instance_name, gpu_instance_zone for wake-signal)
```

The IAM/GPU chicken-and-egg (IAM needs `gpu_instance_id` for the wake-binding
condition, GPU needs `gpu_sa_email` from IAM) is resolved by the IAM module
treating `gpu_instance_id` as optional and the GPU module creating its own
wake-binding via a separate `google_project_iam_member` after the instance
exists. See `terraform/modules/iam/main.tf` `bind_classifier_to_gpu` local.

## Migrating from stub to prod

Once your project + ADC are set up and you're ready to commit to real GCP
spend:

```bash
# 1. First time: confirm credentials work
gcloud auth application-default login

# 2. Run a dry-run against the prod composition to see the real plan
tabula enclave up myenclave --composition prod --project <your-project> --dry-run

# 3. Apply — this creates real billable infrastructure
tabula enclave up myenclave --composition prod --project <your-project> --yes
```

To go back to stub (no-cost dev/testing), drop the `--composition prod` flag
or pass `--composition stub`.
