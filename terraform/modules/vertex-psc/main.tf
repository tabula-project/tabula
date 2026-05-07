###############################################################################
# Vertex AI Private Service Connect (PSC) endpoint
#
# This module exposes Google APIs (including Vertex AI / aiplatform) on a
# private internal IP inside the enclave VPC, so the GPU VM can reach
# `aiplatform.googleapis.com` without traversing Cloud NAT to a public address.
#
# It pairs a `google_compute_global_address` (PSC consumer IP) with a
# `google_compute_global_forwarding_rule` targeting Google's PSC service
# attachment for the chosen API bundle (`all-apis` by default), then layers a
# private DNS zone on top so the standard Vertex AI hostnames resolve to the
# private IP from inside the VPC.
###############################################################################

locals {
  # Resource name prefix shared with the rest of the enclave's GCP resources.
  name_prefix = "${var.enclave_name}-vertex-psc"

  # Hostnames the GPU VM will use to reach Vertex AI. The PSC consumer IP must
  # answer for both the global and regional aiplatform endpoints, plus the
  # generic googleapis.com used by the gcloud SDK.
  vertex_hostnames = [
    "aiplatform.googleapis.com.",
    "${var.region}-aiplatform.googleapis.com.",
    "googleapis.com.",
  ]

  # Map the `psc_target` bundle name to the global PSC service attachment URI
  # Google publishes for each Private Google Access bundle.
  psc_target_uri = {
    "all-apis" = "https://www.googleapis.com/compute/v1/projects/google/global/networks/default/serviceAttachments/all-apis"
    "vpc-sc"   = "https://www.googleapis.com/compute/v1/projects/google/global/networks/default/serviceAttachments/vpc-sc"
  }[var.psc_target]
}

# Reserve an internal IPv4 address inside the VPC for the PSC endpoint.
# PSC for Google APIs uses a *global* address backed by a *global* forwarding
# rule (this is distinct from PSC to a customer-published service, which is
# regional). The address is allocated out of the VPC's auto-mode reservation
# pool unless `psc_address` pins it explicitly.
resource "google_compute_global_address" "psc" {
  project      = var.project_id
  name         = "${local.name_prefix}-ip"
  address_type = "INTERNAL"
  purpose      = "PRIVATE_SERVICE_CONNECT"
  network      = var.vpc_id
  address      = var.psc_address

  description = "PSC consumer IP for Vertex AI (${var.psc_target}) in enclave ${var.enclave_name}."
}

# The forwarding rule is the actual PSC endpoint: traffic to this internal IP
# is forwarded to Google's managed service attachment for the API bundle.
#
# `load_balancing_scheme = ""` (empty string) is required for PSC-to-Google-APIs
# forwarding rules — it explicitly opts out of the standard load-balancing
# schemes. This is the documented contract per Google's PSC docs and is
# distinct from `INTERNAL` / `EXTERNAL` schemes used elsewhere.
resource "google_compute_global_forwarding_rule" "psc" {
  project = var.project_id
  name    = "${local.name_prefix}-fr"

  ip_address            = google_compute_global_address.psc.id
  target                = local.psc_target_uri
  network               = var.vpc_id
  load_balancing_scheme = ""

  description = "PSC forwarding rule to Google APIs (${var.psc_target}) for enclave ${var.enclave_name}."
}

###############################################################################
# Private DNS overrides
#
# Without these zones, the GPU VM resolves `aiplatform.googleapis.com` via
# Google's public DNS, which returns a public IP that Cloud NAT would have to
# route. With the private zones below, the same hostnames resolve to the PSC
# internal IP, so traffic never leaves the VPC.
#
# We create:
#   - a `googleapis.com.` private zone with an A record at the apex, plus
#     wildcard A records for the regional and aiplatform subdomains.
# A single zone is sufficient because GCP's Cloud DNS lets a private zone
# shadow the public zone for VMs in the bound network.
###############################################################################

resource "google_dns_managed_zone" "googleapis" {
  count = var.create_dns_zone ? 1 : 0

  project    = var.project_id
  name       = "${local.name_prefix}-googleapis"
  dns_name   = "googleapis.com."
  visibility = "private"

  description = "PSC override: resolves Google API hostnames to the PSC IP inside enclave ${var.enclave_name}."

  private_visibility_config {
    networks {
      network_url = var.vpc_id
    }
  }
}

# Apex A record: `googleapis.com` -> PSC IP.
resource "google_dns_record_set" "googleapis_apex" {
  count = var.create_dns_zone ? 1 : 0

  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis[0].name
  name         = "googleapis.com."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.psc.address]
}

# Wildcard A record covers `aiplatform.googleapis.com`,
# `<region>-aiplatform.googleapis.com`, and any other Google API hostname
# (e.g., logging, monitoring) that uses the same PSC IP.
resource "google_dns_record_set" "googleapis_wildcard" {
  count = var.create_dns_zone ? 1 : 0

  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis[0].name
  name         = "*.googleapis.com."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.psc.address]
}
