# Stub for issue #21 (gitea). Replace with the real Gitea VM module when
# #21 merges.

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

variable "service_account" {
  type = string
}

output "instance_name" {
  description = "Gitea instance name (synthetic)."
  value       = "tabula-${var.enclave_name}-gitea"
}

output "internal_ip" {
  description = "Internal IP of the Gitea VM (synthetic; RFC 5737)."
  value       = "203.0.113.30"
}
