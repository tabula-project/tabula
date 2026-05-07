output "budget_id" {
  description = "Resource ID of the google_billing_budget."
  value       = google_billing_budget.enclave.id
}

output "budget_display_name" {
  description = "Human-readable budget name."
  value       = google_billing_budget.enclave.display_name
}

output "pubsub_topic" {
  description = "Pub/Sub topic that receives budget threshold notifications."
  value       = google_pubsub_topic.budget_alerts.name
}

output "pubsub_topic_id" {
  description = "Fully-qualified Pub/Sub topic ID."
  value       = google_pubsub_topic.budget_alerts.id
}

output "auto_kill_function_name" {
  description = "Name of the Cloud Function that stops enclave VMs on budget breach."
  value       = google_cloudfunctions2_function.auto_kill.name
}

output "auto_kill_service_account_email" {
  description = "Email of the SA the auto-kill function runs as. Tightly scoped to compute.instances.stop only."
  value       = google_service_account.auto_kill.email
}

output "state_bucket" {
  description = "Cloud Storage bucket where the cost_killed marker is written."
  value       = local.state_bucket_name
}

output "state_object_path" {
  description = "Object key the auto-kill function writes inside state_bucket."
  value       = "enclaves/${var.enclave_name}/state.json"
}

output "monitoring_notification_channels" {
  description = "Map of email -> notification channel ID created for budget alerts."
  value       = { for e, c in google_monitoring_notification_channel.email : e => c.id }
}
