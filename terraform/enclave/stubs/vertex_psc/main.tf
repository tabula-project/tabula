# Stub for issue #24 (vertex-psc). Replace with the real Vertex AI Private
# Service Connect module when #24 merges.

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "enclave_name" {
  type = string
}

variable "network_id" {
  type = string
}

output "psc_endpoint" {
  description = "Vertex AI PSC endpoint name (synthetic)."
  value       = "tabula-${var.enclave_name}-vertex-psc"
}
