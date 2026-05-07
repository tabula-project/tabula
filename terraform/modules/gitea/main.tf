###############################################################################
# Tabula enclave Gitea module.
#
# Stands up the private Gitea forge inside the enclave:
#   - GCE VM (no external IP), tagged for the egress allowlist + ingress firewall.
#   - Separate persistent data disk mounted at /var/lib/gitea, NOT auto-deleted
#     on instance delete (survives in-place VM rebuilds within an enclave).
#   - Cloud-init that formats the data disk on first boot, installs a pinned
#     Gitea binary, and starts a systemd unit.
#   - Private DNS managed zone for `<enclave_name>.internal` plus an A record
#     `gitea.<enclave_name>.internal` -> instance internal IP.
#
# Out of scope (see issue #21 scope boundaries):
#   - Ingress firewall rules (#20 / #24 key off the enclave-gitea network tag).
#   - HA / replication, GCS backup of the data disk.
#   - First-boot admin user seeding (documented in README; lives in a separate
#     bootstrap concern).
#   - SSH key sync between Gitea and the GPU VM.
#   - TLS termination — internal-only HTTP is acceptable for MVP.
###############################################################################

locals {
  name_prefix = var.enclave_name

  instance_name     = "${local.name_prefix}-gitea"
  data_disk_name    = "${local.name_prefix}-gitea-data"
  dns_zone_name     = "${local.name_prefix}-internal"
  dns_dns_name      = "${var.enclave_name}.internal."
  internal_fqdn     = "gitea.${var.enclave_name}.internal"
  internal_dns_name = "gitea.${var.enclave_name}.internal."

  # device_name on the attached disk surfaces the disk inside the guest at
  # /dev/disk/by-id/google-<device_name>. cloud-init keys off this name.
  data_disk_device_name = "gitea-data"

  # Required tags. Egress allowlist (#14) keys off enclave-workload; ingress
  # firewall (#20/#24) keys off enclave-gitea.
  base_network_tags = ["enclave-workload", "enclave-gitea"]
  network_tags      = distinct(concat(local.base_network_tags, var.extra_network_tags))

  base_labels = {
    enclave   = var.enclave_name
    component = "gitea"
    managed   = "terraform"
  }
  labels = merge(local.base_labels, var.labels)

  cloud_init_rendered = templatefile("${path.module}/cloud-init.yaml", {
    gitea_version         = var.gitea_version
    data_disk_device_name = local.data_disk_device_name
    internal_fqdn         = local.internal_fqdn
    extra_user_data       = var.cloud_init_extra_user_data
  })
}

###############################################################################
# Persistent data disk
#
# Created independently of the instance so we can attach it with
# auto_delete = false. This survives in-place VM rebuilds (the standard GCP
# pattern). On `terraform destroy`, the disk is destroyed along with the
# instance — that is the intended teardown semantics for an enclave.
###############################################################################

resource "google_compute_disk" "data" {
  project = var.project_id
  zone    = var.zone
  name    = local.data_disk_name
  type    = var.data_disk_type
  size    = var.data_disk_size_gb

  description = "Persistent /var/lib/gitea data disk for ${local.instance_name}. Survives in-place VM rebuilds; destroyed with the enclave."

  labels = local.labels
}

###############################################################################
# Gitea VM
###############################################################################

resource "google_compute_instance" "gitea" {
  project = var.project_id
  zone    = var.zone
  name    = local.instance_name

  machine_type = var.machine_type

  tags   = local.network_tags
  labels = local.labels

  boot_disk {
    auto_delete = true
    initialize_params {
      image  = "${var.image_project}/${var.image_family}"
      size   = var.boot_disk_size_gb
      type   = var.boot_disk_type
      labels = local.labels
    }
  }

  attached_disk {
    source      = google_compute_disk.data.self_link
    device_name = local.data_disk_device_name
    mode        = "READ_WRITE"
  }

  network_interface {
    subnetwork = var.subnet_self_link
    # No access_config block => no external IP. Egress flows via Cloud NAT
    # from the network module (#14); ingress is restricted by #20/#24.
  }

  service_account {
    email = var.gitea_sa_email
    # cloud-platform is the standard scope; actual access is gated by the
    # SA's IAM bindings (declared in #15, not here).
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
    user-data      = local.cloud_init_rendered
  }

  # Tolerate small metadata edits (e.g., an operator updating user-data) without
  # forcing a full instance replacement that would unmount the data disk.
  lifecycle {
    ignore_changes = [
      metadata["user-data"],
    ]
  }
}

###############################################################################
# Private DNS — `<enclave_name>.internal`
#
# This module owns the zone because Gitea is the first internal-DNS consumer
# in the enclave. If a second module needs the zone later (per issue notes),
# factor it out into a `dns/` module and have both consumers depend on its
# outputs.
###############################################################################

resource "google_dns_managed_zone" "internal" {
  project = var.project_id

  name        = local.dns_zone_name
  dns_name    = local.dns_dns_name
  description = "Private DNS zone for the ${var.enclave_name} enclave. Resolves only inside the attached VPC network."

  visibility = "private"

  private_visibility_config {
    networks {
      network_url = var.network_self_link
    }
  }

  labels = local.labels
}

resource "google_dns_record_set" "gitea_a" {
  project = var.project_id

  managed_zone = google_dns_managed_zone.internal.name
  name         = local.internal_dns_name
  type         = "A"
  ttl          = 300

  rrdatas = [google_compute_instance.gitea.network_interface[0].network_ip]
}
