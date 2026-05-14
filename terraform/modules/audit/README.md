# Tabula enclave audit module

Audit logging foundation for the Tabula enclave. Captures every audit-relevant
event from the enclave into destinations that **stay inside the enclave's GCP
project**. Audit telemetry leaving the project would silently widen the trust
boundary; the module is the structural enforcement of that constraint.

Closes the audit-logging acceptance criteria from `tabula-project/tabula#38`.

## What it creates

- A dedicated **Cloud Logging bucket** `enclave-audit-<name>` in the enclave's
  project (`google_logging_project_bucket_config`), with configurable
  retention (default **90 days**).
- A **`google_logging_project_sink`** routing audit + workload application
  logs into that bucket. Uses `unique_writer_identity = true`; the writer SA
  is granted `roles/logging.bucketWriter` **only** on the audit bucket via an
  IAM condition.
- (Optional, default on) A **GCS export bucket**
  `gs://<project>-enclave-audit-<name>/` with **lifecycle: Nearline after
  30 days**, plus a second sink (`google_logging_project_sink`) exporting the
  same filtered logs there. The sink writer SA gets
  `roles/storage.objectCreator` on the export bucket only.
- A `google_project_iam_audit_config` entry enabling **`DATA_READ`** and
  **`DATA_WRITE`** audit logging for `aiplatform.googleapis.com` so Vertex AI
  model invocations are captured. Body is **not** logged — metadata only.

Both buckets carry the labels `enclave = <name>` and `managed-by = terraform`
so the `down` cleanup flow (`#28`) can find and remove them.

## Audit log scope

The combined sink filter covers:

| In-scope event family                  | How it lands in the filter                                                                                         |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **VPC flow logs**                      | `resource.type="gce_subnetwork" AND logName:"compute.googleapis.com%2Fvpc_flows"`                                  |
| **IAM admin activity**                 | `logName:"cloudaudit.googleapis.com"` matches `cloudaudit.googleapis.com/activity`                                 |
| **IAM data access**                    | same `cloudaudit.googleapis.com` term + the `google_project_iam_audit_config` entries (project-wide, see below)    |
| **GCE start/stop/delete**              | `cloudaudit.googleapis.com/activity` for compute.googleapis.com                                                    |
| **Vertex AI `DATA_READ` / `DATA_WRITE`** | `cloudaudit.googleapis.com/data_access` once the module's `google_project_iam_audit_config` is applied            |
| **IAP / SSH-tunnelled sessions**       | IAP + Compute API audit logs land under `cloudaudit.googleapis.com`                                                |
| **Application logs (Ops Agent)**       | `resource.type="gce_instance" AND labels.enclave="<name>"` (workload modules MUST set the `enclave` instance label) |

`var.extra_log_filter_terms` lets you append extra OR'd filter terms without
forking the module. Use sparingly — overly broad filters increase log volume
and cost.

### Vertex AI audit config

The audit config (`google_project_iam_audit_config` for
`aiplatform.googleapis.com`) is **project-level**, not module-scoped. Without
it, `cloudaudit.googleapis.com/data_access` is silent for Vertex AI calls and
the sink filter has nothing to match. The module owns this config by default;
set `enable_vertex_data_access_audit = false` only if another module in the
same project is already managing it (otherwise the providers will fight over
ownership).

### Workload Ops Agent

This module assumes workload VMs (#17 classifier, #19/#35 GPU, #21 Gitea) run
the **Google Cloud Ops Agent** with their cloud-init. The Ops Agent ships
application logs (`bootstrap.log`, `ollama.log`, `claude.log`, `gitea.log`)
into Cloud Logging under `resource.type=gce_instance`. The workload modules
also stamp the `enclave = <name>` GCE instance label so this module's sink
filter picks the right VMs out without grabbing unrelated GCE workloads in
the same project.

A copy-paste cloud-init snippet for the workload modules:

```yaml
runcmd:
  - curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
  - bash add-google-cloud-ops-agent-repo.sh --also-install
```

The instance label `enclave: <name>` MUST be present on every workload VM
(set it via the GCE module's `labels = { enclave = var.enclave_name }`).

## Trust boundary — deny-by-default on log-router export

The module owns sinks it creates, but Terraform cannot prevent an operator
from later adding a cross-project sink out of band. Two layers of mitigation:

### 1. Self-check in module (statically)

Both sink destinations are computed from `var.project_id` so any future
edit to `main.tf` that introduces a non-project destination is reviewable in
diff. The outputs `logging_sink_destination` and `gcs_sink_destination` are
intentionally surfaced for the consuming root module / CI to assert against.

A simple consumer-side check:

```hcl
check "no_cross_project_audit_sinks" {
  assert {
    condition     = startswith(module.audit.logging_sink_destination, "logging.googleapis.com/projects/${var.project_id}/")
    error_message = "Audit Logging sink destination is outside project ${var.project_id} — exfil incident."
  }

  assert {
    condition     = module.audit.gcs_sink_destination == null || startswith(module.audit.gcs_sink_destination, "storage.googleapis.com/${var.project_id}-")
    error_message = "Audit GCS export sink destination is outside project ${var.project_id} — exfil incident."
  }
}
```

### 2. Post-deploy smoke check (operationally)

After `up`, the operator MUST run:

```sh
gcloud logging sinks list --project "$PROJECT_ID" --format='value(destination)' \
  | grep -v "projects/$PROJECT_ID/" \
  | grep -v "$PROJECT_ID-" \
  | { ! grep . ; }
```

The pipeline succeeds (exit 0) **only** when every sink destination references
`projects/$PROJECT_ID/...` (Logging buckets) or starts with `$PROJECT_ID-`
(GCS buckets). Any other line is a cross-project sink and is treated as an
exfil incident: page the on-call, do not just log it.

> Future hardening (out of scope here): organization policy
> `constraints/logging.disableDefaultSinkRetention` and an org-level
> `gcloud asset feed` watching for new sinks. Both are tracked separately.

## Inputs

| Name                              | Type           | Default     | Description                                                                                       |
| --------------------------------- | -------------- | ----------- | ------------------------------------------------------------------------------------------------- |
| `project_id`                      | `string`       | (required)  | GCP project ID hosting the enclave. Audit logs MUST stay inside this project.                     |
| `enclave_name`                    | `string`       | (required)  | Short name used as a resource prefix and on the `enclave` label.                                  |
| `logging_bucket_location`         | `string`       | `"global"`  | Cloud Logging bucket location.                                                                    |
| `logging_bucket_retention_days`   | `number`       | `90`        | Retention in days for the Cloud Logging bucket.                                                   |
| `gcs_export_enabled`              | `bool`         | `true`      | Whether to create the GCS export sink + bucket.                                                   |
| `gcs_bucket_location`             | `string`       | `"US"`      | Location for the GCS audit export bucket.                                                         |
| `gcs_nearline_after_days`         | `number`       | `30`        | Age (days) at which GCS export objects transition to NEARLINE.                                    |
| `gcs_force_destroy`               | `bool`         | `false`     | Allow `tofu destroy` to delete the GCS bucket even if non-empty. Keep `false` in production. |
| `extra_log_filter_terms`          | `list(string)` | `[]`        | Extra OR'd filter terms appended to the sink filter.                                              |
| `enable_vertex_data_access_audit` | `bool`         | `true`      | Enable `DATA_READ`/`DATA_WRITE` audit logging for `aiplatform.googleapis.com`.                    |
| `labels`                          | `map(string)`  | `{}`        | Extra labels merged into the default `{ enclave, managed-by, purpose }` label set.                |

## Outputs

| Name                              | Description                                                                                                |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `logging_bucket_id`               | Resource ID of the Cloud Logging bucket.                                                                   |
| `logging_bucket_name`             | Name of the Cloud Logging bucket (e.g., `enclave-audit-prod`).                                             |
| `logging_bucket_location`         | Location of the Cloud Logging bucket.                                                                      |
| `logging_bucket_retention_days`   | Configured retention (days) for the Cloud Logging bucket.                                                  |
| `logging_sink_name`               | Name of the in-project Logging sink.                                                                       |
| `logging_sink_writer_identity`    | Writer SA for the Logging sink. Scoped via IAM condition to the audit bucket only.                         |
| `logging_sink_destination`        | Destination URI of the Logging sink. Use this in a consumer-side `check` block to detect exfil.            |
| `gcs_bucket_name`                 | GCS export bucket name (null if disabled).                                                                 |
| `gcs_bucket_url`                  | gs:// URL of the GCS export bucket (null if disabled).                                                     |
| `gcs_sink_name`                   | Name of the GCS export sink (null if disabled).                                                            |
| `gcs_sink_writer_identity`        | Writer SA for the GCS sink. Scoped to `roles/storage.objectCreator` on the export bucket only.             |
| `gcs_sink_destination`            | Destination URI of the GCS sink (null if disabled). Use this in a consumer-side `check` block to detect exfil. |
| `audit_filter`                    | The combined log filter both sinks use. Use as a starting point for Logs Explorer queries.                 |
| `vertex_data_access_audit_enabled`| Whether `DATA_READ` / `DATA_WRITE` audit logging is enabled for `aiplatform.googleapis.com`.               |

## How to query a wake event end-to-end

A wake event spans classifier (#17, #37) → GPU VM (#19, #35) → Vertex AI
(#23). The combined filter captures all three; here are sample queries you
can paste into the Logs Explorer (after selecting the
`enclave-audit-<name>` bucket as the scope):

1. **Classifier emits the wake signal** (Ops Agent application log):

   ```
   resource.type="gce_instance"
   labels.enclave="<name>"
   jsonPayload.event="wake_signal_emitted"
   ```

2. **GPU VM start** (GCE activity audit):

   ```
   logName:"cloudaudit.googleapis.com/activity"
   protoPayload.methodName="v1.compute.instances.start"
   resource.labels.instance_id=~"^gpu-"
   ```

3. **Vertex AI model invocation** (data_access audit, requires
   `enable_vertex_data_access_audit = true`):

   ```
   logName:"cloudaudit.googleapis.com/data_access"
   protoPayload.serviceName="aiplatform.googleapis.com"
   protoPayload.methodName=~".*Predict.*|.*GenerateContent.*"
   ```

4. **GPU VM response logs** (Ops Agent application log):

   ```
   resource.type="gce_instance"
   labels.enclave="<name>"
   jsonPayload.app="claude"
   ```

Pivot between them with `protoPayload.authenticationInfo.principalEmail`
(the GPU SA from #15) and the wake-event correlation ID emitted by the
classifier.

## Validation

The repo uses [OpenTofu](https://opentofu.org/) (`tofu`); `terraform` works as
a fallback. The CLI invocations below show `tofu`.

```sh
cd terraform/modules/audit/examples/basic
cp terraform.tfvars.example terraform.tfvars   # edit project_id
tofu init
tofu validate
tofu plan
```

`tofu validate` runs without GCP credentials. `tofu plan` requires
ADC pointing at a real project but does not mutate anything.

After `tofu apply`, run the post-deploy smoke check:

```sh
gcloud logging sinks list --project "$PROJECT_ID" --format='value(destination)' \
  | grep -v "projects/$PROJECT_ID/" \
  | grep -v "$PROJECT_ID-" \
  | { ! grep . ; }
```

Exit 0 means no cross-project sinks. Anything else is an exfil incident.

## Non-goals (handled elsewhere)

- **No Langfuse / external observability integration** — would itself be
  cross-project exfil. Out of scope.
- **No L4 prompt/response body logging** — L4 is end-to-end encrypted (Epic
  #13). Logging plaintext prompts would defeat the encryption guarantee.
  Vertex audit logs metadata only.
- **No SIEM integration** — the GCS export is the integration point if
  needed later.
- **No log redaction / DLP** — threat model (b) treats GCP as trusted.
- **No log-based alerting** — overlaps with cost guardrails (#39).
- **No tamper-evident / write-once logging** — useful future hardening.
- **No Ops Agent install** — workload modules (`#17`, `#19/#35`, `#21`)
  install the agent themselves; this module only consumes its output.
- **No remote state backend** — caller's responsibility.
