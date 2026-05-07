output "classifier_sa_email" {
  description = "Email of the classifier VM service account. Pass to the classifier VM module's service_account.email."
  value       = google_service_account.classifier.email
}

output "gpu_sa_email" {
  description = "Email of the GPU VM service account. Pass to the GPU VM module's service_account.email."
  value       = google_service_account.gpu.email
}

output "gitea_sa_email" {
  description = "Email of the Gitea VM service account. Pass to the Gitea VM module's service_account.email."
  value       = google_service_account.gitea.email
}

output "cli_operator_sa_email" {
  description = "Email of the optional CI/CLI operator service account. null when var.create_cli_operator_sa is false."
  value       = var.create_cli_operator_sa ? google_service_account.cli_operator[0].email : null
}

output "gpu_waker_role_id" {
  description = "Fully qualified ID of the custom \"GPU waker\" role created by this module. The GPU VM module can use this when adding the classifier->GPU wake binding after the fact (when gpu_instance_id was not yet known at IAM-module-apply time)."
  value       = google_project_iam_custom_role.gpu_waker.id
}

output "classifier_sa_member" {
  description = "Pre-formatted IAM member string for the classifier SA (\"serviceAccount:<email>\"). Convenience output for downstream modules that need to add their own bindings (e.g., GPU VM module adding the wake permission)."
  value       = "serviceAccount:${google_service_account.classifier.email}"
}
