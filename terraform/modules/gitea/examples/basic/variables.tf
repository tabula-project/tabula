variable "project_id" {
  description = "GCP project ID to deploy the example into."
  type        = string
}

variable "region" {
  description = "GCP region for the example deployment."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone within var.region for the Gitea VM and data disk."
  type        = string
  default     = "us-central1-a"
}

variable "enclave_name" {
  description = "Enclave name used for resource naming, tagging, and the internal DNS suffix."
  type        = string
  default     = "demo"
}

# The example accepts pre-built network and IAM inputs as if produced by the
# real network (#14) and IAM (#15) modules. Validation does not require these
# resources to exist; supplying placeholder strings is enough for `terraform
# validate`. For `terraform plan`, point them at the real outputs.

variable "network_self_link" {
  description = "Self-link of the VPC network. Use the network module's `vpc_self_link` output in real deployments."
  type        = string
  default     = "projects/REPLACE_ME/global/networks/REPLACE_ME-vpc"
}

variable "subnet_self_link" {
  description = "Self-link of the workload subnet. Use the network module's `subnet_self_link` output in real deployments."
  type        = string
  default     = "projects/REPLACE_ME/regions/us-central1/subnetworks/REPLACE_ME-workload"
}

variable "gitea_sa_email" {
  description = "Gitea service-account email. Use the IAM module's `gitea_sa_email` output in real deployments."
  type        = string
  default     = "gitea-sa@REPLACE_ME.iam.gserviceaccount.com"
}
