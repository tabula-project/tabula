variable "project_id" {
  description = "GCP project ID hosting the enclave VPC."
  type        = string
}

variable "region" {
  description = "GCP region for the regional subnet, Cloud Router, and Cloud NAT."
  type        = string
}

variable "enclave_name" {
  description = "Short name used to prefix resource names and the workload network tag (e.g., 'prod', 'dev01')."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{0,30}[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be a valid GCE name fragment: lowercase letters, digits, and hyphens, 1-32 chars, starting with a letter."
  }
}

variable "subnet_cidr" {
  description = "Primary IPv4 CIDR for the workload subnet."
  type        = string
  default     = "10.10.0.0/24"
}

variable "nat_log_filter" {
  description = "Cloud NAT log filter. One of ERRORS_ONLY, TRANSLATIONS_ONLY, or ALL."
  type        = string
  default     = "ERRORS_ONLY"

  validation {
    condition     = contains(["ERRORS_ONLY", "TRANSLATIONS_ONLY", "ALL"], var.nat_log_filter)
    error_message = "nat_log_filter must be one of ERRORS_ONLY, TRANSLATIONS_ONLY, or ALL."
  }
}

variable "flow_log_sampling" {
  description = "VPC flow-log sampling rate for the workload subnet, in [0.0, 1.0]."
  type        = number
  default     = 0.5

  validation {
    condition     = var.flow_log_sampling >= 0 && var.flow_log_sampling <= 1
    error_message = "flow_log_sampling must be between 0.0 and 1.0 inclusive."
  }
}

variable "extra_egress_cidrs" {
  description = <<-EOT
    Documented exceptions to the default egress allowlist. Each entry creates an
    additional egress allow firewall rule scoped to the workload network tag.
    Use sparingly; every entry weakens the enclave egress posture.
  EOT
  type = list(object({
    cidr        = string
    ports       = list(string) # e.g., ["443"], or ["all"] to allow all TCP/UDP
    description = string
  }))
  default = []
}
