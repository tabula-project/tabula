# IAP CIDR for SSH tunneling. Stable, well-known range.
# Source: https://cloud.google.com/iap/docs/using-tcp-forwarding#before_you_begin
locals {
  iap_cidr = "35.235.240.0/20"
}

# ------------------------------------------------------------------------------
# Ingress: external Noise port (the only externally reachable surface)
# ------------------------------------------------------------------------------
resource "google_compute_firewall" "allow_noise_ingress" {
  project     = var.project_id
  network     = var.network
  name        = "${var.name_prefix}-allow-noise-ingress"
  description = "Allow inbound Noise XX traffic from the public internet to the classifier VM. This is the ONLY rule with source_ranges = 0.0.0.0/0."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.classifier_tag]

  allow {
    protocol = "tcp"
    ports    = [tostring(var.noise_port)]
  }
}

# ------------------------------------------------------------------------------
# Ingress: SSH via IAP only (no public SSH ever)
# ------------------------------------------------------------------------------
resource "google_compute_firewall" "allow_iap_ssh" {
  count = var.enable_iap_ssh ? 1 : 0

  project     = var.project_id
  network     = var.network
  name        = "${var.name_prefix}-allow-iap-ssh"
  description = "Allow SSH (TCP/22) from the GCP IAP CIDR to enclave workload VMs. Operator SSH path; replaces public SSH ingress."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges = [local.iap_cidr]
  target_tags   = [var.workload_tag]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

# ------------------------------------------------------------------------------
# Ingress: intra-VPC, classifier -> GPU on the wake-signal port
# ------------------------------------------------------------------------------
resource "google_compute_firewall" "allow_classifier_to_gpu" {
  project     = var.project_id
  network     = var.network
  name        = "${var.name_prefix}-allow-classifier-to-gpu"
  description = "Allow the classifier to send the wake-signal to the GPU VM. Source/target are tags, not CIDRs."
  direction   = "INGRESS"
  priority    = 1000

  source_tags = [var.classifier_tag]
  target_tags = [var.gpu_tag]

  allow {
    protocol = "tcp"
    ports    = [tostring(var.wake_signal_port)]
  }
}

# ------------------------------------------------------------------------------
# Ingress: intra-VPC, GPU -> Gitea on Gitea HTTP port
# ------------------------------------------------------------------------------
resource "google_compute_firewall" "allow_gpu_to_gitea" {
  project     = var.project_id
  network     = var.network
  name        = "${var.name_prefix}-allow-gpu-to-gitea"
  description = "Allow the GPU VM to reach Gitea on its HTTP port for git operations during a session."
  direction   = "INGRESS"
  priority    = 1000

  source_tags = [var.gpu_tag]
  target_tags = [var.gitea_tag]

  allow {
    protocol = "tcp"
    ports    = [tostring(var.gitea_port)]
  }
}

# ------------------------------------------------------------------------------
# Ingress: explicit deny-all (priority 65534, matching GCP implicit-deny)
# Makes the allowlist posture explicit and audit-friendly.
# ------------------------------------------------------------------------------
resource "google_compute_firewall" "deny_all_ingress" {
  project     = var.project_id
  network     = var.network
  name        = "${var.name_prefix}-deny-all-ingress"
  description = "Explicit deny-all ingress. Priority 65534 matches GCP's implicit-deny. Makes allowlist posture explicit."
  direction   = "INGRESS"
  priority    = 65534

  source_ranges = ["0.0.0.0/0"]

  deny {
    protocol = "all"
  }
}
