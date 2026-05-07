output "instance_name" {
  description = "Name of the GPU GCE instance. Classifier needs this to issue a wake (compute.instances.start)."
  value       = google_compute_instance.gpu.name
}

output "instance_id" {
  description = "Fully-qualified resource ID of the GPU instance (projects/.../zones/.../instances/...)."
  value       = google_compute_instance.gpu.id
}

output "instance_self_link" {
  description = "Self-link of the GPU instance. Useful for IAM conditions that scope compute.instances.start to this instance."
  value       = google_compute_instance.gpu.self_link
}

output "internal_ip" {
  description = "Primary internal IPv4 address of the GPU instance (no external IP is assigned by this module)."
  value       = google_compute_instance.gpu.network_interface[0].network_ip
}

output "zone" {
  description = "Zone the GPU instance is pinned to. Classifier needs this to issue a wake."
  value       = google_compute_instance.gpu.zone
}

output "stop_schedule_id" {
  description = "Resource policy ID of the unconditional stop schedule attached to the GPU instance."
  value       = google_compute_resource_policy.gpu_stop.id
}

output "network_tags" {
  description = "Network tags applied to the instance. Surfaced so firewall modules can compose without hardcoding."
  value       = google_compute_instance.gpu.tags
}
