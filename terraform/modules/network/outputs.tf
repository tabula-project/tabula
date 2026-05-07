output "vpc_id" {
  description = "Numeric/string ID of the enclave VPC (google_compute_network.id)."
  value       = google_compute_network.vpc.id
}

output "vpc_self_link" {
  description = "Self-link of the enclave VPC."
  value       = google_compute_network.vpc.self_link
}

output "vpc_name" {
  description = "Name of the enclave VPC."
  value       = google_compute_network.vpc.name
}

output "subnet_id" {
  description = "ID of the workload subnet."
  value       = google_compute_subnetwork.workload.id
}

output "subnet_self_link" {
  description = "Self-link of the workload subnet."
  value       = google_compute_subnetwork.workload.self_link
}

output "subnet_name" {
  description = "Name of the workload subnet."
  value       = google_compute_subnetwork.workload.name
}

output "subnet_cidr" {
  description = "Primary IPv4 CIDR of the workload subnet."
  value       = google_compute_subnetwork.workload.ip_cidr_range
}

output "nat_router_name" {
  description = "Name of the Cloud Router used by Cloud NAT."
  value       = google_compute_router.router.name
}

output "nat_name" {
  description = "Name of the Cloud NAT gateway."
  value       = google_compute_router_nat.nat.name
}

output "region" {
  description = "Region the subnet, router, and NAT are deployed in."
  value       = var.region
}

output "workload_network_tag" {
  description = "Network tag applied to workload VMs and referenced by firewall rules. Downstream modules MUST tag VMs with this string."
  value       = "${var.enclave_name}-workload"
}
