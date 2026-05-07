output "psc_ip" {
  description = "Internal IPv4 address of the PSC endpoint. Vertex AI hostnames resolve here from inside the VPC."
  value       = google_compute_global_address.psc.address
}

output "psc_endpoint_name" {
  description = "Name of the global forwarding rule that fronts the PSC endpoint."
  value       = google_compute_global_forwarding_rule.psc.name
}

output "psc_address_self_link" {
  description = "Self-link of the reserved PSC internal IP (useful for cross-module references)."
  value       = google_compute_global_address.psc.self_link
}

output "psc_dns_zone_name" {
  description = "Name of the private DNS zone overriding Google API hostnames, or null if not created."
  value       = var.create_dns_zone ? google_dns_managed_zone.googleapis[0].name : null
}

output "vertex_hostnames" {
  description = "Vertex AI hostnames that the private DNS zone overrides to the PSC IP."
  value       = local.vertex_hostnames
}
