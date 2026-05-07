# Stub for issue #17 (classifier). Replace with the real classifier VM module
# when #17 merges.

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

# Use TEST-NET-3 (203.0.113.0/24, RFC 5737) so it's clear at a glance this
# is documentation/synthetic, never a real address.
output "external_ip" {
  description = "External IP of the classifier VM (synthetic; RFC 5737)."
  value       = "203.0.113.10"
}

output "noise_port" {
  description = "Noise protocol UDP port the classifier listens on."
  value       = 51820
}

output "instance_name" {
  description = "Compute Engine instance name (synthetic)."
  value       = "tabula-${var.enclave_name}-classifier"
}
