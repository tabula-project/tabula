variable "project_id" {
  description = "GCP project ID to deploy the example into."
  type        = string
}

variable "region" {
  description = "GCP region for the example deployment."
  type        = string
  default     = "us-central1"
}

variable "enclave_name" {
  description = "Enclave name used for resource naming and tagging."
  type        = string
  default     = "demo"
}
