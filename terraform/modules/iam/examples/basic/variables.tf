variable "project_id" {
  description = "GCP project ID for the enclave."
  type        = string
}

variable "enclave_name" {
  description = "Short identifier for this enclave (e.g., \"prod\", \"dev\")."
  type        = string
  default     = "dev"
}
