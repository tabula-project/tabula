###############################################################################
# Tabula classifier VM
#
# Always-on, cheap-warm front door of the enclave. Terminates Noise (server
# code lands in Epic #13), runs a small OSS classifier model under Ollama for
# haiku-class routing decisions, and signals the GPU VM to wake when a request
# needs the heavy agent.
#
# This module ONLY creates the VM and bootstrap. IAM bindings, firewall rules,
# wake-signal logic, and Noise terminator code live in their own modules /
# issues. See module README.
###############################################################################

locals {
  # Required tags so firewall rules in the ingress-firewall module can target
  # this VM unambiguously. User-supplied tags are unioned in.
  required_tags = ["enclave-workload", "enclave-classifier"]
  network_tags  = distinct(concat(local.required_tags, var.network_tags))

  cloud_init_user_data = templatefile(
    "${path.module}/cloud-init.yaml.tftpl",
    {
      model_name        = var.model_name
      gpu_instance_name = var.gpu_instance_name
      gpu_instance_zone = var.gpu_instance_zone
    }
  )

  module_metadata = {
    # GCE understands `user-data` as the cloud-init payload when the image
    # ships with cloud-init (Debian 12 cloud images do).
    user-data = local.cloud_init_user_data

    # Surface the classifier model and wake target as metadata so operators
    # can see them in the GCP console without SSHing.
    "tabula-classifier-model" = var.model_name
    "tabula-wake-gpu-target"  = var.gpu_instance_name == "" ? "unset" : "${var.gpu_instance_name}@${var.gpu_instance_zone}"

    # Block project-wide SSH keys; rely on IAP + OS Login (separate CLI issue
    # handles tunnelling). Keeps the SSH surface narrow.
    block-project-ssh-keys = "TRUE"
    enable-oslogin         = "TRUE"
  }

  # User-supplied metadata wins on key collision so callers can override.
  effective_metadata = merge(local.module_metadata, var.metadata)
}

resource "google_compute_instance" "classifier" {
  project      = var.project_id
  name         = var.name
  machine_type = var.machine_type
  zone         = var.zone

  tags                = local.network_tags
  labels              = var.labels
  deletion_protection = var.deletion_protection

  boot_disk {
    initialize_params {
      image = "${var.image_project}/${var.image_family}"
      size  = var.disk_size_gb
      type  = var.disk_type
      labels = merge(var.labels, {
        role = "classifier-boot"
      })
    }
  }

  network_interface {
    subnetwork = var.subnet_id
    # Intentionally no `access_config` block — the VM has NO external IP.
    # Egress is via Cloud NAT (configured in the VPC module); SSH is via IAP
    # (configured in the ingress-firewall module + a separate CLI issue).
  }

  service_account {
    email  = var.service_account_email
    scopes = var.service_account_scopes
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = local.effective_metadata

  # The cloud-init user-data is the entire bootstrap; replacing it should
  # rebuild the VM rather than mutate it in place.
  lifecycle {
    create_before_destroy = false
  }
}
