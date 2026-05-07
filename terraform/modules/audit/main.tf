###############################################################################
# Tabula enclave audit module
#
# Creates an in-project Cloud Logging bucket and (optionally) a GCS export
# bucket to capture every audit-relevant event from the enclave. Both
# destinations are inside the same GCP project as the enclave itself: the
# trust constraint is that audit telemetry MUST NOT leave the project.
#
# Scope captured by the sinks (see README.md for the full filter rationale):
#
#   - cloudaudit.googleapis.com/activity      (IAM, GCE, etc.)
#   - cloudaudit.googleapis.com/data_access   (Vertex AI DATA_READ/DATA_WRITE,
#                                              IAP SSH session events)
#   - cloudaudit.googleapis.com/system_event  (managed-system events)
#   - compute.googleapis.com VPC flow logs
#   - workload application logs from the Ops Agent on classifier/GPU/Gitea
#
# Trust boundary: the only sink destinations created here are
#   (1) `logging.googleapis.com/projects/<this-project>/locations/.../buckets/...`
#   (2) `storage.googleapis.com/<this-project>-...`
# Both are scoped to var.project_id. Operators MUST run the smoke-check
# `gcloud logging sinks list --project <p> --format='value(destination)' \
#    | grep -v "<p>"` after `up` to verify no out-of-project sinks exist.
###############################################################################

locals {
  name_prefix         = var.enclave_name
  logging_bucket_name = "enclave-audit-${local.name_prefix}"
  gcs_bucket_name     = "${var.project_id}-enclave-audit-${local.name_prefix}"
  logging_sink_name   = "enclave-audit-${local.name_prefix}-to-logging"
  gcs_sink_name       = "enclave-audit-${local.name_prefix}-to-gcs"

  base_labels = {
    enclave    = var.enclave_name
    managed-by = "terraform"
    purpose    = "enclave-audit"
  }
  resource_labels = merge(local.base_labels, var.labels)

  # Core audit + workload-app log filter. Designed so each `OR` clause covers
  # a single in-scope event family from the issue body.
  base_filter_terms = concat(
    [
      # Cloud Audit Logs — covers IAM activity, IAM data_access, GCE
      # lifecycle, IAP SSH, and Vertex AI metadata once data_access audit
      # logging is enabled (see google_project_iam_audit_config below).
      "logName:\"cloudaudit.googleapis.com\"",

      # VPC flow logs (subnet-emitted; resource.type is gce_subnetwork).
      "resource.type=\"gce_subnetwork\" AND logName:\"compute.googleapis.com%2Fvpc_flows\"",

      # Workload application logs from the Ops Agent. The Ops Agent writes
      # under logName ".../logs/<app>" with resource.type=gce_instance, so
      # gate on instance label `enclave` (set by the workload modules) to
      # avoid pulling in unrelated VMs that may live in the same project.
      "resource.type=\"gce_instance\" AND labels.enclave=\"${var.enclave_name}\"",
    ],
    var.extra_log_filter_terms,
  )

  combined_filter = join(" OR ", [for t in local.base_filter_terms : "(${t})"])
}

###############################################################################
# Cloud Logging bucket (in-project; retention configurable, default 90 days)
###############################################################################

resource "google_logging_project_bucket_config" "audit" {
  project        = var.project_id
  location       = var.logging_bucket_location
  bucket_id      = local.logging_bucket_name
  retention_days = var.logging_bucket_retention_days
  description    = "Tabula enclave audit log bucket for ${var.enclave_name}. Retention enforced at ${var.logging_bucket_retention_days}d."
}

###############################################################################
# Sink: route enclave audit + app logs into the in-project Logging bucket.
#
# `unique_writer_identity = true` makes Logging mint a dedicated SA per sink
# that we can scope tightly. The writer needs roles/logging.bucketWriter on
# the destination bucket.
###############################################################################

resource "google_logging_project_sink" "to_logging_bucket" {
  project                = var.project_id
  name                   = local.logging_sink_name
  description            = "Routes ${var.enclave_name} audit + app logs to the in-project Cloud Logging bucket. Destination MUST stay within ${var.project_id}."
  destination            = "logging.googleapis.com/projects/${var.project_id}/locations/${var.logging_bucket_location}/buckets/${google_logging_project_bucket_config.audit.bucket_id}"
  filter                 = local.combined_filter
  unique_writer_identity = true
}

resource "google_project_iam_member" "logging_sink_writer" {
  project = var.project_id
  role    = "roles/logging.bucketWriter"
  member  = google_logging_project_sink.to_logging_bucket.writer_identity

  condition {
    title       = "Restrict to enclave audit bucket"
    description = "Sink writer may only write to the enclave audit Logging bucket."
    expression  = "resource.name.endsWith(\"/buckets/${local.logging_bucket_name}\")"
  }
}

###############################################################################
# Optional: GCS export bucket + sink (Nearline-after-30d lifecycle)
###############################################################################

resource "google_storage_bucket" "audit_export" {
  count = var.gcs_export_enabled ? 1 : 0

  project                     = var.project_id
  name                        = local.gcs_bucket_name
  location                    = var.gcs_bucket_location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.gcs_force_destroy

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = var.gcs_nearline_after_days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  labels = local.resource_labels
}

resource "google_logging_project_sink" "to_gcs_bucket" {
  count = var.gcs_export_enabled ? 1 : 0

  project                = var.project_id
  name                   = local.gcs_sink_name
  description            = "Exports ${var.enclave_name} audit + app logs to in-project GCS bucket ${local.gcs_bucket_name}. Destination MUST stay within ${var.project_id}."
  destination            = "storage.googleapis.com/${google_storage_bucket.audit_export[0].name}"
  filter                 = local.combined_filter
  unique_writer_identity = true
}

resource "google_storage_bucket_iam_member" "gcs_sink_writer" {
  count = var.gcs_export_enabled ? 1 : 0

  bucket = google_storage_bucket.audit_export[0].name
  role   = "roles/storage.objectCreator"
  member = google_logging_project_sink.to_gcs_bucket[0].writer_identity
}

###############################################################################
# Vertex AI DATA_READ / DATA_WRITE audit config.
#
# Without this, model invocations against aiplatform.googleapis.com do not
# emit data_access audit logs and the sink filter above will not see them.
# This is a project-level config, not module-scoped — tracked here because
# it is a hard requirement of the issue's audit scope.
###############################################################################

resource "google_project_iam_audit_config" "vertex" {
  count = var.enable_vertex_data_access_audit ? 1 : 0

  project = var.project_id
  service = "aiplatform.googleapis.com"

  audit_log_config {
    log_type = "DATA_READ"
  }

  audit_log_config {
    log_type = "DATA_WRITE"
  }
}
