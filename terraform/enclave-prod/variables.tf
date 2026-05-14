# Production composition variables.
#
# Mirrors `terraform/enclave/variables.tf` (project_id, region, enclave_name)
# and adds the inputs the real sibling modules require which the stubs didn't
# need (zone for VMs, noise_port for firewall/output echo). Defaults are
# chosen to match the original epic #12 design choices: us-central1-a (T4 GPU
# availability), TCP 7000 for the public Noise port (firewall module default).

variable "project_id" {
  description = "GCP project ID that owns the enclave's resources."
  type        = string
}

variable "region" {
  description = "GCP region to place the enclave in (e.g. us-central1)."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone within `region` for VM-bearing modules (classifier, gpu, gitea). Must offer Tesla T4 quota."
  type        = string
  default     = "us-central1-a"
}

variable "enclave_name" {
  description = "DNS-safe enclave name, 3-30 chars, lowercase, validated by the CLI."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{1,28}[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be DNS-safe: lowercase, start with a letter, end with a letter or digit, 3-30 chars, hyphens allowed in the middle."
  }
}

variable "noise_port" {
  description = "TCP port the classifier VM listens on for the Noise XX handshake. The only port reachable from 0.0.0.0/0."
  type        = number
  default     = 7000

  validation {
    condition     = var.noise_port > 0 && var.noise_port < 65536
    error_message = "noise_port must be a valid TCP port (1-65535)."
  }
}
