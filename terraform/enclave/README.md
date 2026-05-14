# `terraform/enclave/` — stub composition (offline-plan-able)

This is the **offline-plan** composition: every sibling module reference
points at a stub under `./stubs/<name>/` that emits synthetic outputs and
makes zero GCP API calls. `tofu plan` and `apply` (or `terraform plan` /
`apply` as a fallback) succeed against it with no credentials. It's the
default that `tabula enclave up` runs.

> **IaC tool**: the repo migrated from Terraform (BUSL since v1.6) to
> [OpenTofu](https://opentofu.org/) (MPL 2.0) in #96. The directory name
> `terraform/` is retained because it is OpenTofu's de-facto convention
> too; only the binary changed. The CLI prefers `tofu` and falls back to
> `terraform` if you haven't installed OpenTofu yet.

**Looking for the real-GCP composition?** It lives at
[`../enclave-prod/`](../enclave-prod/README.md) and is selected via
`tabula enclave up --composition prod`. The two compositions emit the same
output names (`classifier_ip`, `noise_port`, `enclave_name`) so
`tabula enclave status` and the state-file format work unchanged across both.

| Property | this dir (stub) | `terraform/enclave-prod/` |
|---|---|---|
| Offline `tofu plan` | ✅ | ❌ (needs ADC) |
| Real GCP resources on apply | ❌ (synthetic outputs) | ✅ |
| Default for `tabula enclave up` | ✅ (no flag) | `--composition prod` |
| CI / test use | ✅ | needs sandbox project |

## What composes here

| Sibling module | Source issue | Status here |
|---|---|---|
| network    | #14 (closed) | stub at `./stubs/network`    |
| iam        | #15 (closed) | stub at `./stubs/iam`        |
| classifier | #17 (closed) | stub at `./stubs/classifier` |
| gpu        | #19 (closed) | stub at `./stubs/gpu`        |
| gitea      | #21 (closed) | stub at `./stubs/gitea`      |
| firewall   | #23 (closed) | stub at `./stubs/firewall`   |
| vertex-psc | #24 (closed) | stub at `./stubs/vertex_psc` |

### Why two compositions exist

Originally this directory was an integration point waiting for sibling modules
to land. Once they did, the question became: wire them in place (losing the
offline-plan property) or keep the stubs and add a real-GCP sibling? Issue
#107 picked Option B (two compositions), preserving offline-plan-ability
for CI and dev while making real apply available behind a flag. See
`../enclave-prod/README.md` for the rationale.

### Stub contract

Each `./stubs/<name>/` module:

1. Declares the same input variables we expect the real modules to accept.
2. Emits synthetic outputs with the same names and types real modules
   would provide (`classifier_ip`, `noise_port`, etc.). The synthetic IPs
   are deliberately drawn from RFC 5737 TEST-NET-3 (`203.0.113.0/24`) so
   it's clear at a glance they are documentation/synthetic.
3. Makes zero cloud API calls — `tofu plan` and `tofu apply` (or the
   `terraform` equivalents) succeed with no GCP credentials needed.

### Inputs

| Variable        | Type   | Notes |
|---|---|---|
| `project_id`    | string | GCP project; stubs ignore this. |
| `region`        | string | GCP region; stubs ignore this. |
| `enclave_name`  | string | DNS-safe enclave name (validated by the CLI). |

### Outputs

| Output          | Type   | Source              |
|---|---|---|
| `classifier_ip` | string | `module.classifier.external_ip` (stub: `"203.0.113.10"`) |
| `noise_port`    | number | `module.classifier.noise_port`  (stub: `51820`)         |

These are the two values the CLI's `up` command surfaces in its success
summary; they are also the values `tabula enclave status` (separate issue)
will read from `state.json`.

### Hand-off contract for the sibling-module issues

Each sibling-module PR should:

1. Replace the corresponding `./stubs/<name>` reference in `main.tf` with
   the real module path and uncomment any real arguments.
2. Keep the input-variable names listed in the stub's `variables.tf`
   (those are the contract).
3. Keep the output names listed in the stub's `outputs.tf`.
4. Update this README's table.

### Local validation

The CLI's `--dry-run` end-to-end test invokes:

```bash
tabula enclave up smoke --dry-run --project test --region us-central1
```

which copies this directory to `~/.tabula/enclaves/smoke/terraform/`, runs
`tofu init` (or `terraform init` as a fallback), then `tofu plan`. With
only stubs wired up, this must succeed offline.
