# GPU VM module for the Tabula enclave.
#
# Creates a GPU-equipped GCE instance that is "cold by default": it is created
# in a TERMINATED state, woken on demand by the classifier (separate issue),
# and unconditionally stopped on a recurring schedule (default every 30 min).
#
# Out of scope (see issue #19 scope boundaries):
#   - claude CLI installation, git clone (Bootstrap issue)
#   - Wake signal mechanism (separate sub-issue)
#   - Firewall rules (network module #14 / ingress sub-issue)
#   - IAM role bindings (IAM module #15)
#
# Locals --------------------------------------------------------------------

locals {
  # Required tags. Firewall rules in #14 / ingress sub-issue key off these.
  base_network_tags = ["enclave-workload", "enclave-gpu"]
  network_tags      = distinct(concat(local.base_network_tags, var.extra_network_tags))

  instance_name        = "${var.enclave_name}-gpu"
  resource_policy_name = "${var.enclave_name}-gpu-stop-schedule"

  base_labels = {
    enclave   = var.enclave_name
    component = "gpu"
    managed   = "terraform"
  }
  labels = merge(local.base_labels, var.labels)
}

# Stop-only instance schedule -----------------------------------------------
#
# Per issue notes: the simplest "cold by default" implementation is to
# unconditionally stop the VM on a fixed cadence. The wake signal (separate
# issue) restarts it on demand. This means a long-running task may be killed
# mid-flight; that is acceptable for the MVP per the locked architecture.
#
# Provider note: google_compute_resource_policy supports vm_stop_schedule
# without a sibling vm_start_schedule, which is exactly what we want.
resource "google_compute_resource_policy" "gpu_stop" {
  project = var.project_id
  region  = var.region
  name    = local.resource_policy_name

  description = "Unconditional stop schedule for ${local.instance_name}; on-demand wake is handled by the classifier."

  instance_schedule_policy {
    time_zone = var.stop_schedule_timezone

    vm_stop_schedule {
      schedule = var.stop_schedule_cron
    }
    # Intentionally no vm_start_schedule: start is on-demand via wake signal.
  }
}

# GPU instance --------------------------------------------------------------

resource "google_compute_instance" "gpu" {
  project = var.project_id
  zone    = var.zone
  name    = local.instance_name

  machine_type = var.machine_type

  # Cold by default: created in TERMINATED state so `terraform apply` does not
  # incur GPU cost until the first wake signal arrives.
  desired_status = "TERMINATED"

  # Required for guest accelerators (and to allow stop-for-update edits).
  allow_stopping_for_update = true

  tags = local.network_tags

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

  network_interface {
    subnetwork = var.subnet_self_link
    # No access_config block => no external IP. Egress goes via Cloud NAT
    # from the network module (#14).
  }

  guest_accelerator {
    type  = var.accelerator_type
    count = var.accelerator_count
  }

  # GCE requires TERMINATE on host maintenance for any instance with attached
  # GPUs; setting it explicitly prevents a plan-time error.
  scheduling {
    on_host_maintenance = "TERMINATE"
    automatic_restart   = true
    preemptible         = false
  }

  service_account {
    email = var.gpu_sa_email
    # cloud-platform is the standard scope; actual access is gated by the
    # SA's IAM bindings (declared in #15, not here).
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = var.startup_script != "" ? var.startup_script : null

  # Attach the stop schedule. The provider exposes this as a list on
  # google_compute_instance.resource_policies.
  resource_policies = [google_compute_resource_policy.gpu_stop.id]

  # The instance schedule policy must exist before the instance references it,
  # and we don't want desired_status flapping on minor metadata edits.
  lifecycle {
    ignore_changes = [
      # Bootstrap issue may push a new startup script; tolerate it without
      # forcing a replace here.
      metadata_startup_script,
    ]
  }
}
