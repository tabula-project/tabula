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
  description = "Short name used to prefix resource names (e.g., 'prod', 'dev01'). Max 17 chars to leave room for the 'psc' suffix on the PSC forwarding rule (GCP cap = 20 chars total, alphanumeric only)."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{0,30}[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be a valid GCE name fragment: lowercase letters, digits, and hyphens, 1-32 chars, starting with a letter."
  }

  validation {
    # PSC forwarding rule for Google APIs must be ≤20 chars, alphanumeric
    # only (no hyphens). We construct it as `${var.enclave_name}psc`.
    # `replace` strips any hyphens from enclave_name so the resulting FR name
    # is still alphanumeric, but we still need the input to be short enough.
    condition     = length(replace(var.enclave_name, "-", "")) <= 17
    error_message = "enclave_name (with hyphens stripped) must be ≤17 chars so the derived PSC forwarding rule name '${replace(var.enclave_name, "-", "")}psc' fits GCP's 20-char cap."
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
    Explicit internal IPv4 address for the PSC endpoint. REQUIRED for
    `purpose = PRIVATE_SERVICE_CONNECT`; auto-allocation is NOT supported
    by GCP for this purpose (confirmed by error "Invalid value for field
    'resource.address': ''" when null is passed).

    Default is `192.168.99.99`, an RFC 1918 IP that doesn't overlap with
    the network module's default workload subnet (10.10.0.0/24). Override
    if your VPC layout uses a conflicting range.
  EOT
  type        = string
  default     = "192.168.99.99"

  validation {
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.psc_address))
    error_message = "psc_address must be a valid IPv4 dotted-decimal address."
  }
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
