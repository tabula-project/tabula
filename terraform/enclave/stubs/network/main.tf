# Stub for issue #14 (network). No providers, no resources.
# Replace with the real network module when #14 merges.

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "enclave_name" {
  type = string
}

output "network_id" {
  description = "VPC network self-link / id (synthetic)."
  value       = "projects/${var.project_id}/global/networks/tabula-${var.enclave_name}-stub"
}

output "subnet_id" {
  description = "Subnet self-link / id (synthetic)."
  value       = "projects/${var.project_id}/regions/${var.region}/subnetworks/tabula-${var.enclave_name}-stub"
}
