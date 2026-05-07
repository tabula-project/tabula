output "instance_name" {
  description = "Name of the classifier instance."
  value       = google_compute_instance.classifier.name
}

output "internal_ip" {
  description = "Primary internal (RFC1918) IP of the classifier VM. The VM has no external IP."
  value       = google_compute_instance.classifier.network_interface[0].network_ip
}

output "zone" {
  description = "GCP zone the instance was created in."
  value       = google_compute_instance.classifier.zone
}

output "self_link" {
  description = "GCE self-link for the instance."
  value       = google_compute_instance.classifier.self_link
}

output "network_tags" {
  description = "Network tags applied to the instance (firewall rules can target these)."
  value       = google_compute_instance.classifier.tags
}

output "service_account_email" {
  description = "Service account email attached to the instance (passed through from input)."
  value       = var.service_account_email
}
