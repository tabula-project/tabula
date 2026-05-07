variable "project_id" {
  description = "GCP project ID hosting the enclave. Audit logs MUST stay inside this project."
  type        = string
}

variable "enclave_name" {
  description = "Short name used to prefix resource names and tag resources for `down` cleanup (e.g., 'prod', 'dev01')."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{0,30}[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be a valid GCE/GCS name fragment: lowercase letters, digits, and hyphens, 1-32 chars, starting with a letter."
  }
}

variable "logging_bucket_location" {
  description = "Location for the Cloud Logging bucket. Must be a Logging-supported location (region or 'global'). Default 'global' matches GCP's default audit bucket behaviour."
  type        = string
  default     = "global"
}

variable "logging_bucket_retention_days" {
  description = "Retention in days for the Cloud Logging bucket. Default 90 per the trust-boundary policy."
  type        = number
  default     = 90

  validation {
    condition     = var.logging_bucket_retention_days >= 1 && var.logging_bucket_retention_days <= 3650
    error_message = "logging_bucket_retention_days must be between 1 and 3650 (10 years)."
  }
}

variable "gcs_export_enabled" {
  description = "Whether to create the GCS export sink + bucket. Disable if you only need the in-project Logging bucket."
  type        = bool
  default     = true
}

variable "gcs_bucket_location" {
  description = "Location for the GCS audit export bucket. SHOULD match the enclave's region to avoid cross-region transfer."
  type        = string
  default     = "US"
}

variable "gcs_nearline_after_days" {
  description = "Age in days after which GCS export objects transition to NEARLINE storage class."
  type        = number
  default     = 30

  validation {
    condition     = var.gcs_nearline_after_days >= 1
    error_message = "gcs_nearline_after_days must be >= 1."
  }
}

variable "gcs_force_destroy" {
  description = "Whether `terraform destroy` may delete the GCS audit bucket even if non-empty. False by default to preserve audit history; flip to true only for ephemeral test enclaves."
  type        = bool
  default     = false
}

variable "extra_log_filter_terms" {
  description = <<-EOT
    Optional extra log-filter OR-terms appended to the sink filter. Each entry
    is wrapped in parentheses and OR'd into the filter, so for example
    `["resource.type=\"gce_instance\""]` will widen the sink to include all
    GCE instance logs. Use sparingly — overly broad filters increase log
    volume and cost.
  EOT
  type        = list(string)
  default     = []
}

variable "enable_vertex_data_access_audit" {
  description = "Whether to enable DATA_READ/DATA_WRITE audit logging for aiplatform.googleapis.com. Required to see Vertex AI model invocations in audit logs."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Extra labels to apply to created resources, in addition to the default `enclave = <name>` and `managed-by = terraform` labels."
  type        = map(string)
  default     = {}
}
