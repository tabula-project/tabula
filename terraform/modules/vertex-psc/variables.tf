variable "project_id" {
  description = "GCP project ID hosting the enclave VPC and the PSC endpoint."
  type        = string
}

variable "region" {
  description = <<-EOT
    GCP region for the PSC forwarding rule and Vertex AI traffic. Used to construct
    the regional aiplatform hostname (`<region>-aiplatform.googleapis.com`) that the
    private DNS zone overrides.
  EOT
  type        = string
}

variable "enclave_name" {
  description = "Short name used to prefix resource names (e.g., 'prod', 'dev01')."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{0,30}[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be a valid GCE name fragment: lowercase letters, digits, and hyphens, 1-32 chars, starting with a letter."
  }
}

variable "vpc_id" {
  description = "Self-link or ID of the enclave VPC network the PSC endpoint attaches to."
  type        = string
}

variable "subnet_id" {
  description = <<-EOT
    Self-link or ID of the workload subnet whose region/IP range the PSC internal
    address is reserved within. Must be in `var.region`.
  EOT
  type        = string
}

variable "psc_target" {
  description = <<-EOT
    PSC service attachment target for Google APIs. Use `all-apis` (the default) to
    reach every Google API privately, or `vpc-sc` if VPC Service Controls bundles
    are required. Vertex AI is included in both bundles; `all-apis` is the standard
    choice unless there is a specific reason to lock down.
  EOT
  type        = string
  default     = "all-apis"

  validation {
    condition     = contains(["all-apis", "vpc-sc"], var.psc_target)
    error_message = "psc_target must be one of: all-apis, vpc-sc."
  }
}

variable "psc_address" {
  description = <<-EOT
    Optional explicit internal IPv4 address for the PSC endpoint. If null, GCP
    auto-allocates an address from the VPC's internal range. Pinning is useful
    when DNS records are managed outside this module.
  EOT
  type        = string
  default     = null
}

variable "create_dns_zone" {
  description = <<-EOT
    Whether to create the private DNS zone that overrides Vertex AI hostnames to
    resolve to the PSC IP from inside the VPC. Disable only if a parent module
    manages a shared `googleapis.com` private zone.
  EOT
  type        = bool
  default     = true
}
