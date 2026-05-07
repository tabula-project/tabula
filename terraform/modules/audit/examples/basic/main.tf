###############################################################################
# Basic example invocation of the tabula enclave audit module.
#
# This example uses local state and is intended for `terraform validate` /
# `terraform plan` smoke testing. Real deployments wire the audit module into
# the per-enclave root module alongside network/iam.
###############################################################################

provider "google" {
  project = var.project_id
}

module "audit" {
  source = "../../"

  project_id   = var.project_id
  enclave_name = var.enclave_name

  # Defaults are sensible; override here if needed.
  # logging_bucket_location       = "global"
  # logging_bucket_retention_days = 90
  # gcs_export_enabled            = true
  # gcs_bucket_location           = "US"
  # gcs_nearline_after_days       = 30
}

output "logging_bucket_name" {
  value = module.audit.logging_bucket_name
}

output "logging_sink_destination" {
  value = module.audit.logging_sink_destination
}

output "gcs_bucket_name" {
  value = module.audit.gcs_bucket_name
}

output "gcs_sink_destination" {
  value = module.audit.gcs_sink_destination
}

output "audit_filter" {
  value = module.audit.audit_filter
}
