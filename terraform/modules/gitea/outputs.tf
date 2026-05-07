output "instance_name" {
  description = "Name of the Gitea GCE instance."
  value       = google_compute_instance.gitea.name
}

output "instance_id" {
  description = "Fully qualified resource ID of the Gitea instance (projects/.../zones/.../instances/...)."
  value       = google_compute_instance.gitea.id
}

output "instance_self_link" {
  description = "Self-link of the Gitea instance."
  value       = google_compute_instance.gitea.self_link
}

output "internal_ip" {
  description = "Primary internal IPv4 address of the Gitea instance. Same value as the A record but exposed directly for callers that need to bypass DNS (e.g., debugging)."
  value       = google_compute_instance.gitea.network_interface[0].network_ip
}

output "internal_fqdn" {
  description = "Fully qualified internal DNS name for Gitea (`gitea.<enclave_name>.internal`). The GPU VM and other consumers should reference this name, not the IP."
  value       = "gitea.${var.enclave_name}.internal"
}

output "data_disk_id" {
  description = "Resource ID of the persistent data disk. Surfaced so the operator can take snapshots, attach to a replacement instance, or audit auto_delete state."
  value       = google_compute_disk.data.id
}

output "data_disk_self_link" {
  description = "Self-link of the persistent data disk."
  value       = google_compute_disk.data.self_link
}

output "zone" {
  description = "Zone the Gitea instance and data disk are pinned to."
  value       = google_compute_instance.gitea.zone
}

output "dns_zone_name" {
  description = "Name of the private DNS managed zone created by this module. Other modules that need to add records to `<enclave_name>.internal` should reference this zone."
  value       = google_dns_managed_zone.internal.name
}

output "dns_zone_dns_name" {
  description = "Trailing-dot DNS name of the private zone (e.g., 'prod.internal.')."
  value       = google_dns_managed_zone.internal.dns_name
}

output "network_tags" {
  description = "Network tags applied to the Gitea instance. Surfaced so firewall modules can compose without hardcoding."
  value       = google_compute_instance.gitea.tags
}
