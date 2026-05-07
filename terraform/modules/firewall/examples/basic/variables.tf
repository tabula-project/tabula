variable "project_id" {
  description = "GCP project ID."
  type        = string
  default     = "tabula-dev-example"
}

variable "network" {
  description = "VPC network name or self-link. In a real deployment this comes from module.vpc.network_self_link."
  type        = string
  default     = "tabula-enclave-vpc"
}
