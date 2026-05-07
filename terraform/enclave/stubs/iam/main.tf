# Stub for issue #15 (iam). Replace with the real iam module when #15 merges.

variable "project_id" {
  type = string
}

variable "enclave_name" {
  type = string
}

output "classifier_sa_email" {
  description = "Classifier service-account email (synthetic)."
  value       = "tabula-${var.enclave_name}-classifier@${var.project_id}.iam.gserviceaccount.com"
}

output "gpu_sa_email" {
  description = "GPU service-account email (synthetic)."
  value       = "tabula-${var.enclave_name}-gpu@${var.project_id}.iam.gserviceaccount.com"
}

output "gitea_sa_email" {
  description = "Gitea service-account email (synthetic)."
  value       = "tabula-${var.enclave_name}-gitea@${var.project_id}.iam.gserviceaccount.com"
}
