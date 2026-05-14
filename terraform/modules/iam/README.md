# `terraform/modules/iam` — Tabula enclave service accounts + least-privilege IAM

Foundation IAM module for the Tabula GCP enclave. Creates one dedicated service
account per workload VM (classifier, GPU, Gitea), an optional CI/CLI operator
service account, and the minimum project-scoped IAM bindings each one needs.

Parent epic: [#12 — GCP Tabula enclave infrastructure](../../../../../issues/12).

## Why this module exists

Per the locked threat model in #12 — *"GCP trusted as deployment substrate, but
wire and intermediates untrusted; least-privilege still enforced so a
compromise of one VM does not leak into another"* — every workload VM needs
its own service account with the minimum IAM bindings required for its job.
The GPU VM in particular is the principal control behind the *"Vertex AI only,
never public Anthropic API"* property: if its SA holds `roles/storage.*` or
`roles/iam.*`, that property is no longer enforced by the platform.

This module is consumed by every workload-VM module in the enclave. Landing it
early avoids three VM modules each redeclaring overlapping IAM and drifting
apart over time.

## Inputs

| Variable | Type | Default | Required | Description |
|---|---|---|---|---|
| `project_id` | string | — | yes | GCP project the enclave lives in. All bindings are project-scoped. |
| `enclave_name` | string | — | yes | Short DNS-label-compatible identifier (1-30 chars, lowercase + hyphens). Namespaces SA IDs and the custom role. |
| `gpu_instance_id` | string | `null` | no | Fully qualified GPU instance ID (`projects/<p>/zones/<z>/instances/<n>`). When set, the classifier SA gets the `gpu_waker` custom role conditioned to this single instance. When unset, the wake binding is skipped here and is expected to be added later by the GPU VM module. |
| `create_cli_operator_sa` | bool | `false` | no | Create an additional unbound SA for CI/CLI workflows. |

## Outputs

- `classifier_sa_email` — pass to the classifier VM module
- `gpu_sa_email` — pass to the GPU VM module
- `gitea_sa_email` — pass to the Gitea VM module
- `cli_operator_sa_email` — `null` unless `create_cli_operator_sa = true`
- `gpu_waker_role_id` — id of the custom role; the GPU VM module references this when adding the wake binding after the fact
- `classifier_sa_member` — pre-formatted `serviceAccount:<email>` string for downstream module convenience

## Role-by-role rationale

### Classifier SA (`<enclave>-classifier`)

| Role | Why |
|---|---|
| `roles/logging.logWriter` | Cloud Logging from the VM agent; required for any meaningful audit trail. |
| `roles/monitoring.metricWriter` | Cloud Monitoring metrics from the VM agent. |
| `<enclave>_gpu_waker` (custom, conditional) | The classifier's only data-plane responsibility is to start the GPU VM when a query is judged sensitive enough to need it. We grant exactly two permissions — `compute.instances.get` and `compute.instances.start` — pinned via IAM condition (`resource.name == "<gpu_instance_id>"`) to a single instance. This is strictly tighter than `roles/compute.instanceAdmin.v1`. |

The classifier SA holds **no** Vertex AI roles, **no** Storage roles, and
**no** general Compute roles. Its job is to read text, classify it, and (if
warranted) trigger the wake-signal mechanism in #37. Anything beyond that
violates the threat model.

### GPU SA (`<enclave>-gpu`)

| Role | Why |
|---|---|
| `roles/aiplatform.user` | Calls Vertex AI (Anthropic-on-Vertex) via the Private Service Connect endpoint. This is the *only* outbound data-plane reach the GPU VM has; combined with the egress allowlist from the network module (#14), it makes "Vertex AI only" structurally true. |
| `roles/logging.logWriter` | Cloud Logging from the VM agent. |
| `roles/monitoring.metricWriter` | Cloud Monitoring metrics from the VM agent. |

The GPU SA deliberately holds:
- **No** `roles/storage.*` — the GPU VM must not be able to write to Cloud
  Storage. If it could, "Vertex AI only" is no longer true: a compromised
  process could exfil prompts and responses to a private GCS bucket and from
  there to anywhere.
- **No** `roles/iam.*` — the GPU VM must not be able to mutate IAM (e.g.,
  grant itself more roles, create new SAs, mint tokens for other SAs). This
  closes the lateral-privilege-escalation path.
- **No** broad `roles/compute.*` — the GPU VM does not need to manage other
  Compute resources. The instance-metadata-server reads GCE provides for the
  VM's own metadata are not project IAM bindings and are not affected by this
  exclusion.

### Gitea SA (`<enclave>-gitea`)

| Role | Why |
|---|---|
| `roles/logging.logWriter` | Cloud Logging from the VM agent. |
| `roles/monitoring.metricWriter` | Cloud Monitoring metrics from the VM agent. |

Gitea's persistent disk is attached at instance-creation time via the GCE
instance configuration, not via IAM. Gitea performs no Google API calls in the
data plane (it's the internal git host); it has no Vertex AI, Storage, or
Compute permissions.

### CLI/operator SA (`<enclave>-cli-operator`, optional)

Created with **no** IAM bindings of its own. The intended pattern is that the
project owner grants `roles/iam.serviceAccountTokenCreator` on this SA to a
controlled CI runner identity (or to a small set of human operators), and the
CI/CLI workflow then impersonates this SA to apply the rest of the Tabula
Terraform. Any roles the operator SA itself needs are granted out-of-band by
the project owner — this module deliberately does not encode that policy.

## Custom role: `<enclave>_gpu_waker`

A project-level custom role with exactly the permissions needed to wake one
GPU instance:

- `compute.instances.get`
- `compute.instances.start`

Always paired with an IAM condition that pins the binding to a single instance
name. This is preferable to a conditional binding of `roles/compute.*` because
the custom role itself is auditable: a reader can see at a glance that the
classifier can do *exactly two things* on Compute, and only on one VM.

The role is created at module-apply time regardless of whether
`gpu_instance_id` is supplied — its existence is cheap and lets downstream
modules reference it via `gpu_waker_role_id` to add the binding later.

## Bootstrap ordering: the GPU instance ID

There's a chicken-and-egg between the IAM module and the GPU VM module: the
classifier SA needs a binding scoped to the GPU instance, but the GPU
instance is created by a later module that consumes outputs from this one.

This module resolves that by making `gpu_instance_id` an optional input:

- **First apply** (typical): leave `gpu_instance_id = null`. This module
  creates the SAs and the custom role but skips the wake binding. The GPU VM
  module then creates the GPU instance and adds the wake binding via its own
  `google_project_iam_member` resource (using `gpu_waker_role_id` and
  `classifier_sa_member` from this module's outputs).
- **Subsequent applies**: optionally set `gpu_instance_id` here, in which
  case the binding is owned by this module instead. (The downstream module
  must then *not* also create it; pick one owner.)

Because we use `google_project_iam_member` (not `_iam_binding`), bindings
owned by different modules coexist cleanly without one stripping the other.

## Operational expectations for downstream VM modules

> **Do not use the default Compute Engine service account on any workload
> VM.**

Each downstream VM module (classifier, GPU, Gitea) must set
`service_account.email` on its `google_compute_instance` resource to the
matching `*_sa_email` output from this module, and must also set
`service_account.scopes = ["cloud-platform"]` (relying on IAM, not on the
legacy scope system, for authorization). Failing to do so causes the VM to
inherit the default Compute Engine SA's broad permissions, defeating this
module's purpose.

## Idempotency

Re-applying with no input changes produces a no-op plan. All resources are
keyed by stable IDs derived from `enclave_name` and `project_id`, with no
random suffixes or timestamps. The custom role uses a deterministic role ID
(`<enclave_name_underscored>_gpu_waker`).

## Validation

The repo uses [OpenTofu](https://opentofu.org/) (`tofu`); `terraform` works as
a fallback.

```sh
cd terraform/modules/iam
tofu fmt -check
tofu init -backend=false
tofu validate
```

The `examples/basic/` invocation (see below) provides a concrete configuration
for `tofu plan` against an empty/scratch project.

## Example

```hcl
module "iam" {
  source = "./modules/iam"

  project_id   = "my-tabula-enclave-prod"
  enclave_name = "prod"

  # Leave gpu_instance_id null on the first apply; the GPU VM module
  # will add the wake binding after creating the instance.
  gpu_instance_id = null

  create_cli_operator_sa = false
}

# Later — when wiring the classifier VM:
module "classifier" {
  source = "./modules/classifier"
  # ...
  service_account_email = module.iam.classifier_sa_email
}
```

## Scope boundaries (non-goals)

This module deliberately does **not** handle:

- Workload Identity Federation for external CI (out of scope for #12)
- Audit log sink IAM (separate sub-issue under #12)
- Cross-project IAM (single-project enclave only)
- Human user IAM for operators (handled outside Terraform via the CLI/IAP
  issues under #12)
- Vertex AI PSC endpoint IAM (lives with the PSC sub-issue, #23)
- Firewall rules (separate sub-issue, #24)
