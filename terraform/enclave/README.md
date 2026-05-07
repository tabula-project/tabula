# `terraform/enclave/` — root module

This is the integration point between the `tabula enclave up` CLI (#26) and
the per-component Terraform modules. It composes:

| Sibling module | In-flight issue | Status here |
|---|---|---|
| network    | #14 | stub at `./stubs/network`    |
| iam        | #15 | stub at `./stubs/iam`        |
| classifier | #17 | stub at `./stubs/classifier` |
| gpu        | #19 | stub at `./stubs/gpu`        |
| gitea      | #21 | stub at `./stubs/gitea`      |
| firewall   | #23 | stub at `./stubs/firewall`   |
| vertex-psc | #24 | stub at `./stubs/vertex_psc` |

### Why stubs?

#26 (this issue) is the integration point for epic #12. It must land before
the end-to-end demo can be wired together. The sibling modules (#14, #15,
#17, #19, #21, #23, #24) are being built in parallel and may merge in any
order. Rather than block on all seven, this module references **stub**
sibling modules under `./stubs/` that:

1. Declare the same input variables we expect the real modules to accept.
2. Emit synthetic outputs with the same names and types real modules will
   provide (`classifier_ip`, `noise_port`, etc.).
3. Make zero cloud API calls — `terraform plan` and `terraform apply`
   succeed against them with no GCP credentials needed.

When a real sibling module merges, swap the `source = "./stubs/<name>"`
line in `main.tf` for the real module's path / source. The CLI does not
need to change.

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
`terraform init`, then `terraform plan`. With only stubs wired up, this
must succeed offline.
