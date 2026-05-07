variable "project_id" {
  description = "GCP project ID that owns the enclave's resources."
  type        = string
}

variable "region" {
  description = "GCP region to place the enclave in (e.g. us-central1)."
  type        = string
  default     = "us-central1"
}

variable "enclave_name" {
  description = "DNS-safe enclave name, 3-30 chars, lowercase, validated by the CLI."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{1,28}[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be DNS-safe: lowercase, start with a letter, end with a letter or digit, 3-30 chars, hyphens allowed in the middle."
  }
}
