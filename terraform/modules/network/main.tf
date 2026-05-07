###############################################################################
# Tabula enclave network module
#
# Creates a single custom-mode VPC, a single regional private subnet,
# Cloud Router + Cloud NAT for egress, and an egress allowlist enforced via
# firewall rules. No public IPs are placed on workload VMs by this module.
#
# Trust boundary: this module is the structural enforcement of the enclave's
# "no arbitrary internet egress" posture. The deny-all egress at priority
# 65000 catches anything the allow rules at lower priorities don't match.
###############################################################################

locals {
  name_prefix          = var.enclave_name
  vpc_name             = "${local.name_prefix}-vpc"
  subnet_name          = "${local.name_prefix}-workload"
  router_name          = "${local.name_prefix}-router"
  nat_name             = "${local.name_prefix}-nat"
  workload_network_tag = "${local.name_prefix}-workload"

  # Google API VIP ranges reachable via Private Google Access.
  # See https://cloud.google.com/vpc/docs/configure-private-google-access
  restricted_googleapis_cidr = "199.36.153.4/30"
  private_googleapis_cidr    = "199.36.153.8/30"

  # Apt/pypi bootstrap CIDRs. Prefer Google-mirrored repos so traffic stays
  # within Google's network and respects PSC/Private Google Access posture.
  # packages.cloud.google.com resolves into Google's frontends; documenting
  # the apex CIDRs here as a generous superset is acceptable because the
  # deny-all rule still catches everything outside this set.
  bootstrap_cidrs = [
    "34.96.0.0/20", # Google global frontends used by packages.cloud.google.com
    "34.96.108.128/26",
  ]
}

###############################################################################
# VPC + subnet
###############################################################################

resource "google_compute_network" "vpc" {
  project                         = var.project_id
  name                            = local.vpc_name
  description                     = "Tabula enclave VPC for ${var.enclave_name}"
  auto_create_subnetworks         = false
  routing_mode                    = "REGIONAL"
  delete_default_routes_on_create = false
}

resource "google_compute_subnetwork" "workload" {
  project                  = var.project_id
  name                     = local.subnet_name
  description              = "Workload subnet for ${var.enclave_name} (no external IPs)."
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = var.flow_log_sampling
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

###############################################################################
# Cloud Router + Cloud NAT (egress only; no external IPs on VMs)
###############################################################################

resource "google_compute_router" "router" {
  project = var.project_id
  name    = local.router_name
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = local.nat_name
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.workload.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = var.nat_log_filter
  }
}

###############################################################################
# Egress firewall rules — allowlist with low-priority deny-all backstop.
#
# Priority semantics: lower number == higher precedence. The deny-all rule at
# priority 65000 must be numerically larger than every allow rule.
###############################################################################

# Allow egress to Google APIs (Vertex AI, GCS, Artifact Registry) via the
# `restricted.googleapis.com` VIP. Used by Private Google Access.
resource "google_compute_firewall" "egress_allow_restricted_googleapis" {
  project   = var.project_id
  name      = "${local.name_prefix}-egress-allow-restricted-googleapis"
  network   = google_compute_network.vpc.name
  direction = "EGRESS"
  priority  = 1000

  destination_ranges = [local.restricted_googleapis_cidr]
  target_tags        = [local.workload_network_tag]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  description = "Allow HTTPS to restricted.googleapis.com VIP for Vertex AI / GCS / Artifact Registry."
}

# Allow egress to the `private.googleapis.com` VIP as a fallback for any
# Google API not yet reachable via the restricted VIP.
resource "google_compute_firewall" "egress_allow_private_googleapis" {
  project   = var.project_id
  name      = "${local.name_prefix}-egress-allow-private-googleapis"
  network   = google_compute_network.vpc.name
  direction = "EGRESS"
  priority  = 1010

  destination_ranges = [local.private_googleapis_cidr]
  target_tags        = [local.workload_network_tag]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  description = "Allow HTTPS to private.googleapis.com VIP as fallback for Google APIs."
}

# Allow egress to documented apt/pypi bootstrap CIDRs (Google-mirrored).
resource "google_compute_firewall" "egress_allow_bootstrap" {
  project   = var.project_id
  name      = "${local.name_prefix}-egress-allow-bootstrap"
  network   = google_compute_network.vpc.name
  direction = "EGRESS"
  priority  = 1020

  destination_ranges = local.bootstrap_cidrs
  target_tags        = [local.workload_network_tag]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  description = "Allow HTTPS to packages.cloud.google.com / Google-mirrored apt + pypi for VM bootstrap."
}

# Documented exception egress rules. Each entry in var.extra_egress_cidrs
# becomes its own rule so operators can audit them individually.
resource "google_compute_firewall" "egress_allow_extra" {
  for_each = { for idx, e in var.extra_egress_cidrs : idx => e }

  project   = var.project_id
  name      = "${local.name_prefix}-egress-allow-extra-${each.key}"
  network   = google_compute_network.vpc.name
  direction = "EGRESS"
  priority  = 1100 + each.key

  destination_ranges = [each.value.cidr]
  target_tags        = [local.workload_network_tag]

  dynamic "allow" {
    for_each = contains(each.value.ports, "all") ? [1] : []
    content {
      protocol = "tcp"
    }
  }

  dynamic "allow" {
    for_each = contains(each.value.ports, "all") ? [] : [1]
    content {
      protocol = "tcp"
      ports    = each.value.ports
    }
  }

  description = "Documented egress exception: ${each.value.description}"
}

# Deny-all egress backstop. Must remain numerically larger (== lower precedence)
# than every allow rule above. 65000 is conventional.
resource "google_compute_firewall" "egress_deny_all" {
  project   = var.project_id
  name      = "${local.name_prefix}-egress-deny-all"
  network   = google_compute_network.vpc.name
  direction = "EGRESS"
  priority  = 65000

  destination_ranges = ["0.0.0.0/0"]
  target_tags        = [local.workload_network_tag]

  deny {
    protocol = "all"
  }

  description = "Deny-all egress backstop. Anything not matched by an allow rule above is dropped."
}
