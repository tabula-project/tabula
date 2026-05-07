variable "project_id" {
  description = "GCP project to plan against. Does not need to exist for `terraform validate`/`plan` against this stub."
  type        = string
  default     = "tabula-plan-stub"
}

variable "region" {
  description = "Region for the stub plan."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Zone for the stub plan. Must offer the chosen accelerator in a real apply."
  type        = string
  default     = "us-central1-a"
}

variable "enclave_name" {
  description = "Enclave name for resource naming in the stub plan."
  type        = string
  default     = "tabula-stub"
}
