output "logging_bucket_id" {
  description = "Resource ID of the Cloud Logging bucket holding enclave audit + app logs."
  value       = google_logging_project_bucket_config.audit.id
}

output "logging_bucket_name" {
  description = "Name of the Cloud Logging bucket (e.g., 'enclave-audit-prod')."
  value       = google_logging_project_bucket_config.audit.bucket_id
}

output "logging_bucket_location" {
  description = "Location of the Cloud Logging bucket."
  value       = google_logging_project_bucket_config.audit.location
}

output "logging_bucket_retention_days" {
  description = "Configured retention (in days) for the Cloud Logging bucket."
  value       = google_logging_project_bucket_config.audit.retention_days
}

output "logging_sink_name" {
  description = "Name of the project sink routing audit logs into the Logging bucket."
  value       = google_logging_project_sink.to_logging_bucket.name
}

output "logging_sink_writer_identity" {
  description = "Writer identity (service account) for the in-project Logging sink. Granted roles/logging.bucketWriter on the audit bucket only."
  value       = google_logging_project_sink.to_logging_bucket.writer_identity
}

output "logging_sink_destination" {
  description = "Destination URI of the in-project Logging sink. MUST start with `logging.googleapis.com/projects/<project_id>/...` — anything else is an exfil incident."
  value       = google_logging_project_sink.to_logging_bucket.destination
}

output "gcs_bucket_name" {
  description = "Name of the GCS export bucket (null if gcs_export_enabled = false)."
  value       = var.gcs_export_enabled ? google_storage_bucket.audit_export[0].name : null
}

output "gcs_bucket_url" {
  description = "gs:// URL of the GCS export bucket (null if disabled)."
  value       = var.gcs_export_enabled ? google_storage_bucket.audit_export[0].url : null
}

output "gcs_sink_name" {
  description = "Name of the project sink exporting audit logs to GCS (null if gcs_export_enabled = false)."
  value       = var.gcs_export_enabled ? google_logging_project_sink.to_gcs_bucket[0].name : null
}

output "gcs_sink_writer_identity" {
  description = "Writer identity (service account) for the GCS export sink (null if disabled). Granted roles/storage.objectCreator on the export bucket only."
  value       = var.gcs_export_enabled ? google_logging_project_sink.to_gcs_bucket[0].writer_identity : null
}

output "gcs_sink_destination" {
  description = "Destination URI of the GCS export sink. MUST start with `storage.googleapis.com/<project_id>-...` — anything else is an exfil incident."
  value       = var.gcs_export_enabled ? google_logging_project_sink.to_gcs_bucket[0].destination : null
}

output "audit_filter" {
  description = "The combined log filter both sinks use. Useful as a reference for ad-hoc queries in the Logs Explorer."
  value       = local.combined_filter
}

output "vertex_data_access_audit_enabled" {
  description = "Whether DATA_READ/DATA_WRITE audit logging is enabled for aiplatform.googleapis.com."
  value       = var.enable_vertex_data_access_audit
}
