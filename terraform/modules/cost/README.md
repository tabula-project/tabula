# `terraform/modules/cost` — Per-enclave billing budget + auto-stop safety net

Cost guardrails for a Tabula enclave. One enclave = one GCP project, so the
budget is **project-scoped** (not billing-account-wide).

## What this module provisions

| Resource | Purpose |
| --- | --- |
| `google_billing_budget` | Per-project monthly USD budget with 50/90/100% threshold rules |
| `google_pubsub_topic` | Receives budget threshold notifications |
| `google_monitoring_notification_channel` (one per email) | Email recipients for budget alerts |
| `google_cloudfunctions2_function` | Subscribed to the Pub/Sub topic; **stops** workload VMs at 100% |
| `google_service_account` + custom IAM role | Tightly scoped identity for the function (`compute.instances.stop` only) |
| `google_storage_bucket` (optional) | Writes the `cost_killed` marker to `enclaves/<name>/state.json` |

## Budget defaults

| Variable | Default | Notes |
| --- | --- | --- |
| `monthly_budget_usd` | `50` | Intentionally small — fail-safe. Operators must opt-in to larger budgets. |
| Threshold rules | `0.5`, `0.9`, `1.0` | Always notify at 50/90/100% of budget. |
| `kill_threshold_percent` | `100` | At what spend % the auto-kill function actually stops VMs. Lower (e.g. `90`) stops earlier; the 50/90/100 notifications still fire regardless. |
| `workload_label_key` / `workload_label_value` | `enclave-workload` / `true` | Auto-kill only stops VMs carrying this label. Anything else is invisible to it. |

## Auto-stop semantics — STOP is not DESTROY (deliberate)

When the budget breaches `kill_threshold_percent`, the Cloud Function:

1. Lists every instance in the project labeled `enclave-workload=true`.
2. Issues `compute.instances.stop` on each running one (idempotent — already-stopped VMs are skipped).
3. Writes `gs://<state_bucket>/enclaves/<enclave_name>/state.json` with `cost_killed: true`.

It **does not** call `terraform destroy`. It **does not** delete disks, state files, or any other infrastructure. A stopped GPU costs ~zero; preserving disks and logs lets the operator investigate why the budget blew up before deciding to tear down.

The destroy path is gated behind `tabula enclave down` (see issue #28), which is responsible for backing up Terraform state and any persistent disks before teardown.

### Pub/Sub at-least-once delivery — function is idempotent

GCP Pub/Sub guarantees at-least-once delivery, so the function may be invoked multiple times for the same threshold message. The implementation is safe under repeat delivery:

- `_stop_instance_idempotent` checks the current VM status (`STOPPING`, `STOPPED`, `SUSPENDED`, `TERMINATED`) and skips the stop call if the VM is already not running.
- The `state.json` marker is an upsert. The file is small and additive (kill timestamp + reason + which VMs were stopped) — last writer wins, but the message stays consistent.

### What `cost_killed` triggers downstream

- `tabula enclave status` (#30) surfaces the flag prominently in its output.
- `tabula enclave up` (#26) refuses to "resurrect" a `cost_killed` enclave without `--force`. Re-arming requires either `--force` or a fresh enclave (via `down` + `up`), which gets a fresh budget.

## Disabling auto-stop for long-running operator sessions

Sometimes you legitimately need the budget alert without the auto-stop — for example, an extended training run where you've manually approved the higher cost. Set:

```hcl
module "cost" {
  source = "../modules/cost"

  enclave_name      = "long-run-2026-q2"
  project_id        = var.project_id
  billing_account_id = var.billing_account_id

  monthly_budget_usd  = 500   # Still get alerts at 250/450/500
  auto_kill_enabled   = false # But don't stop VMs automatically
  alert_email_addresses = ["ops@example.com"]
}
```

With `auto_kill_enabled = false`:

- The budget, the threshold rules, and the Pub/Sub notifications still fire normally — operators get email at 50/90/100% as usual.
- The Cloud Function runs and logs the threshold breach but does **not** stop any VMs and does **not** write the `cost_killed` marker.

This leaves the safety net inert without disabling visibility. Re-enable by setting `auto_kill_enabled = true` and `terraform apply`-ing.

## Re-arming after an auto-kill

When the operator runs:

```
tabula enclave down <name>   # destroys infra, backs up state + disks
tabula enclave up <name>     # provisions a fresh enclave
```

…the new enclave gets a brand-new budget starting at zero spend. The `cost_killed` marker is written to a per-enclave path (`enclaves/<name>/state.json`) so a fresh provision starts clean.

If you just want to ignore the marker and resume the existing enclave (e.g. you've reviewed the spend and decided it's fine), use `tabula enclave up --force`. That bypasses the resurrection check but does **not** restart the stopped VMs — `tabula enclave start` (or a manual `gcloud compute instances start`) is the explicit second step.

## Usage

```hcl
module "cost" {
  source = "../modules/cost"

  enclave_name        = "alice-dev"
  project_id          = "tabula-alice-dev-9f2a"
  region              = "us-central1"
  billing_account_id  = var.billing_account_id    # never hardcode
  monthly_budget_usd  = 50
  alert_email_addresses = ["alice@example.com"]
}
```

### Required APIs (must be enabled on the project)

- `cloudbilling.googleapis.com`
- `billingbudgets.googleapis.com`
- `pubsub.googleapis.com`
- `cloudfunctions.googleapis.com`
- `cloudbuild.googleapis.com`
- `run.googleapis.com` (Cloud Functions v2 runs on Cloud Run)
- `compute.googleapis.com`
- `monitoring.googleapis.com`
- `storage.googleapis.com`

## Scope boundaries (non-goals)

- **Per-resource / per-VM billing breakdown** — out of scope. Project-level budget only.
- **Dynamic budget adjustment** — not implemented; budget is set at apply time.
- **Predictive alerts** ("projected to exceed budget") — out of scope; static 50/90/100% thresholds are sufficient for MVP.
- **Auto-`terraform destroy`** — explicitly avoided. Destroy is gated behind `tabula enclave down` (#28) which backs up state and disks first.
- **Billing-account-level budgets** — out of scope; this is per-project only.

## Testing

### Terraform

```sh
cd terraform/modules/cost
terraform fmt -recursive -check
terraform init -backend=false
terraform validate
```

### Cloud Function

```sh
cd terraform/modules/cost/auto_kill
pip install pytest functions-framework
pytest -q
```

The unit tests cover:

- 50% / 90% notifications are no-ops (below kill threshold)
- 100% notification stops all `enclave-workload`-labeled, RUNNING VMs
- Unlabeled / wrong-labeled VMs are never touched
- VMs already in `STOPPING`/`STOPPED`/`TERMINATED`/`SUSPENDED` are skipped (idempotency under at-least-once Pub/Sub delivery)
- `auto_kill_enabled = false` master switch suppresses VM stops while preserving alerts
- One failing stop call doesn't abort the rest (partial failure is logged in the marker)
- `state.json` schema matches the contract `tabula enclave status` reads

## File layout

```
terraform/modules/cost/
├── README.md
├── main.tf
├── variables.tf
├── outputs.tf
├── versions.tf
└── auto_kill/
    ├── main.py            # Cloud Function source (entry point: handle_budget_event / main)
    ├── requirements.txt
    └── test_main.py       # pytest unit tests
```

## Related issues

- Parent epic: #12 (GCP enclave infrastructure)
- Hard deps: #14 (VPC), #15 (IAM), #28 (`tabula enclave down`)
- Soft deps: #26 (`up` — `state.json` schema), #30 (`status` — surfaces `cost_killed`)
- Coordinates with: #17, #19, #21 (workload VM modules — must label instances `enclave-workload=true`)
