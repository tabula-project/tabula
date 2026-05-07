# Stub for issue #19 (gpu). Replace with the real GPU VM module when #19 merges.

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
  description = "GPU instance name (synthetic)."
  value       = "tabula-${var.enclave_name}-gpu"
}

output "internal_ip" {
  description = "Internal IP of the GPU VM (synthetic; RFC 5737)."
  value       = "203.0.113.20"
}
