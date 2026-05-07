# `terraform/modules/gpu`

GPU VM for the Tabula enclave. Cold-by-default GCE instance with an attached
NVIDIA T4 (by default), an unconditional stop schedule, and a dedicated
service account that is expected to hold `roles/aiplatform.user` (granted by
the IAM module — not by this module).

Implements issue [#19](https://github.com/tabula-project/tabula/issues/19);
sits under the parent epic [#12](https://github.com/tabula-project/tabula/issues/12).

## Architecture in one paragraph

The GPU VM runs the `claude` CLI against Vertex AI Anthropic models and holds
a working git clone of the substrate. It is **cold by default**: created in
`TERMINATED` state and unconditionally stopped on a recurring schedule
(default every 30 minutes). The classifier VM wakes it on demand via
`compute.instances.start`. There is no external IP; egress flows through
Cloud NAT from the network module. There is no Vertex AI key on disk; access
is via the attached service account.

## What this module owns

- A single `google_compute_instance` (`${enclave_name}-gpu`) with a guest
  accelerator and no external IP.
- A single `google_compute_resource_policy` (`${enclave_name}-gpu-stop-schedule`)
  with a `vm_stop_schedule` (no `vm_start_schedule`) attached to that instance.
- The `enclave-workload` and `enclave-gpu` network tags on the instance, so
  the firewall module can compose against them.

## What this module does **not** own

- IAM bindings for the GPU service account (see issue #15 — this module only
  consumes `gpu_sa_email`).
- Firewall rules (see issue #14 / ingress sub-issue — this module only emits
  network tags).
- `claude` CLI installation, git clone, model caches (see Bootstrap sub-issue
  under #12 — this module exposes a `startup_script` variable so Bootstrap
  can fill it in without replacing this module).
- The wake signal mechanism (separate sub-issue under #12).
- Vertex AI Private Service Connect endpoint (separate sub-issue under #12).

## Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `project_id` | string | yes | — | GCP project hosting the enclave. |
| `region` | string | yes | — | Must match the network module's region. |
| `zone` | string | yes | — | Must offer the chosen accelerator. |
| `enclave_name` | string | yes | — | Prefix for resource naming. |
| `network_self_link` | string | yes | — | From network module output `vpc_self_link`. |
| `subnet_self_link` | string | yes | — | From network module output `subnet_self_link`. |
| `gpu_sa_email` | string | yes | — | From IAM module output `gpu_sa_email`. |
| `machine_type` | string | no | `n1-standard-4` | n1-* required for T4 attach. |
| `accelerator_type` | string | no | `nvidia-tesla-t4` | See "Overriding the GPU type". |
| `accelerator_count` | number | no | `1` | 1–8. |
| `image_family` | string | no | `ubuntu-2204-lts` | Bootstrap will install on top. |
| `image_project` | string | no | `ubuntu-os-cloud` | Source image project. |
| `boot_disk_size_gb` | number | no | `100` | Room for git clone + caches. |
| `boot_disk_type` | string | no | `pd-balanced` | |
| `stop_schedule_cron` | string | no | `*/30 * * * *` | See "Cost model". |
| `stop_schedule_timezone` | string | no | `UTC` | |
| `extra_network_tags` | list(string) | no | `[]` | Appended to `enclave-workload`, `enclave-gpu`. |
| `labels` | map(string) | no | `{}` | Merged into instance + boot disk + policy labels. |
| `startup_script` | string | no | `""` | Reserved for Bootstrap sub-issue. |

## Outputs

| Name | Notes |
|------|-------|
| `instance_name` | Classifier needs this to issue a wake. |
| `instance_id` | Fully-qualified resource ID. |
| `instance_self_link` | Useful for scoping IAM conditions to this single instance. |
| `internal_ip` | Primary internal IPv4 (no external IP is assigned). |
| `zone` | Classifier needs this to issue a wake. |
| `stop_schedule_id` | The attached resource policy. |
| `network_tags` | For firewall composition without hardcoding. |

## Cost model

The GPU VM dominates enclave cost when running, so the module is opinionated
about keeping it stopped:

- **At create time** the instance is created with `desired_status = "TERMINATED"`.
  `terraform apply` provisions the disk and the resource policy but does not
  start the VM, so no GPU-hours accrue.
- **While stopped** you pay for the boot disk only (~$10–15/mo for 100 GB
  pd-balanced, region-dependent). No vCPU, no GPU.
- **While running** the classifier issues a wake (`compute.instances.start`).
  Cost is dominated by the attached GPU (~$0.35/hr for a single T4 in `us-central1`
  on-demand list price as of 2026; check the calculator for your region).
- **Auto-stop** fires per `stop_schedule_cron` (default `*/30 * * * *`,
  i.e. every 30 minutes on the half hour). Worst case the VM runs for ~30
  minutes before being killed. **A long-running request may be killed mid-flight.**
  This is intentional per the architecture's "cold-by-default" intent — if
  you need durable long-running jobs, this module is the wrong abstraction.

If you want a different idle policy (e.g. true idle detection), the
recommended path is a Cloud Scheduler + Cloud Function that calls
`compute.instances.stop` based on a CPU/GPU utilization metric. That lives in
a separate sub-issue, not here.

## Overriding the GPU type

Defaults are `nvidia-tesla-t4` ×1 attached to `n1-standard-4`. To change:

```hcl
module "gpu" {
  source = "../../modules/gpu"

  # ...
  machine_type      = "g2-standard-8"   # L4 requires g2-* family
  accelerator_type  = "nvidia-l4"
  accelerator_count = 1
}
```

Constraints to be aware of:

- T4 (`nvidia-tesla-t4`) and V100 (`nvidia-tesla-v100`) attach to **n1-***
  machine types only. They are not valid on N2/E2/G2.
- L4 (`nvidia-l4`) requires the **g2-*** machine family; it is not valid on n1.
- A100, H100, etc. require A2/A3 machine families and have stricter quota.
- Not every zone in a region offers every accelerator. If `terraform apply`
  fails with `ZONE_RESOURCE_POOL_EXHAUSTED` or `Invalid value for field
  'resource.guestAccelerators[0].acceleratorType'`, pick a different zone.

## GPU quota request

**Fresh GCP projects start with `0` regional GPU quota.** Plan and apply will
both succeed against an empty stub (the resource is declared, not realized
until the API call), but `terraform apply` against a real project without
quota will fail at the `google_compute_instance` create step with an error
similar to:

```
Quota 'NVIDIA_T4_GPUS' exceeded. Limit: 0.0 in region us-central1.
```

To fix, request quota before applying:

1. Console → IAM & Admin → Quotas & System Limits.
2. Filter for `NVIDIA T4 GPUs` (or whichever accelerator you set), constrained
   to your region.
3. Request a limit of at least `accelerator_count` (default 1).
4. T4 quota is per-region and is `0` by default; expect a manual review for
   newer GCP accounts.

If you change `accelerator_type` you must request quota for that specific
SKU; T4 quota does not cover L4 and vice versa.

## Validation

```sh
cd terraform/examples/gpu-only
terraform init
terraform validate
terraform plan
```

`examples/gpu-only` is a deliberately minimal stub that plumbs literal
values (project ID, region, fake SA email) into this module so plan-time
validation does not depend on the network or IAM modules being implemented.

## Manual smoke test (for human reviewer, not CI)

In a sandbox project with T4 quota and a real network/IAM module:

```sh
terraform apply               # instance lands stopped, disk + policy realized
gcloud compute instances start "$(terraform output -raw instance_name)" \
  --zone "$(terraform output -raw zone)"
# wait < 30 min — the scheduled stop policy will halt the instance
gcloud compute instances describe "$(terraform output -raw instance_name)" \
  --zone "$(terraform output -raw zone)" --format='value(status)'
# expect: TERMINATED
```
