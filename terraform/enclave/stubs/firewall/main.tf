# Stub for issue #23 (firewall). Replace with the real firewall module
# when #23 merges.

variable "project_id" {
  type = string
}

variable "enclave_name" {
  type = string
}

variable "network_id" {
  type = string
}

output "firewall_tag" {
  description = "Network tag attached to enclave VMs (synthetic)."
  value       = "tabula-${var.enclave_name}"
}
