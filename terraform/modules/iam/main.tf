###############################################################################
# Tabula enclave — service accounts + least-privilege IAM foundation
#
# This module creates one dedicated service account per workload VM in the
# enclave (classifier, GPU, Gitea) plus an optional human/CI operator SA, and
# attaches the minimum project-scoped IAM bindings each one needs.
#
# Design notes (see README.md for the full rationale):
#   * All bindings use google_project_iam_member, never *_iam_binding or
#     *_iam_policy. The latter are authoritative and would silently strip
#     bindings owned by other modules / by humans operating the project.
#   * No organization-level bindings. Single-project enclave only.
#   * No use of the default Compute Engine service account on any workload VM.
#     Downstream VM modules MUST pass these dedicated SA emails on the VM
#     resource's `service_account.email` attribute.
#   * The classifier needs to start the GPU VM (wake signal). We model that as
#     a custom project-scoped role with only the two permissions required
#     (compute.instances.get + compute.instances.start), and bind it with an
#     IAM condition that scopes the grant to a single instance. This is
#     strictly tighter than roles/compute.instanceAdmin.v1.
###############################################################################

locals {
  # Service account ID prefixes are namespaced by enclave_name so multiple
  # enclaves in the same project (not the supported topology, but a real
  # operational risk during migrations) do not collide on account_id.
  classifier_sa_id   = "${var.enclave_name}-classifier"
  gpu_sa_id          = "${var.enclave_name}-gpu"
  gitea_sa_id        = "${var.enclave_name}-gitea"
  cli_operator_sa_id = "${var.enclave_name}-cli-operator"

  # Custom role ID has tighter character constraints (letters, digits,
  # underscores, dots; max 64 chars). Convert hyphens in enclave_name to
  # underscores for the role ID only.
  gpu_waker_role_id = "${replace(var.enclave_name, "-", "_")}_gpu_waker"

  # Whether to install the classifier->GPU wake binding here. When the GPU
  # instance ID is not yet known (e.g., on the first apply, before the GPU VM
  # module runs), the GPU VM module is responsible for adding the binding
  # later via a separate google_project_iam_member.
  bind_classifier_to_gpu = var.gpu_instance_id != null && var.gpu_instance_id != ""
}

###############################################################################
# Service accounts
###############################################################################

resource "google_service_account" "classifier" {
  project      = var.project_id
  account_id   = local.classifier_sa_id
  display_name = "Tabula classifier (${var.enclave_name})"
  description  = "Tabula classifier VM (enclave: ${var.enclave_name}). Roles: logWriter, metricWriter, scoped GPU wake. See terraform/modules/iam/README.md."
}

resource "google_service_account" "gpu" {
  project      = var.project_id
  account_id   = local.gpu_sa_id
  display_name = "Tabula GPU worker (${var.enclave_name})"
  description  = "Tabula GPU VM (enclave: ${var.enclave_name}). Roles: aiplatform.user, logWriter, metricWriter. No storage/iam/compute. See terraform/modules/iam/README.md."
}

resource "google_service_account" "gitea" {
  project      = var.project_id
  account_id   = local.gitea_sa_id
  display_name = "Tabula Gitea (${var.enclave_name})"
  description  = "Tabula Gitea VM (enclave: ${var.enclave_name}). Roles: logWriter, metricWriter. Disk attached via instance config. See terraform/modules/iam/README.md."
}

resource "google_service_account" "cli_operator" {
  count        = var.create_cli_operator_sa ? 1 : 0
  project      = var.project_id
  account_id   = local.cli_operator_sa_id
  display_name = "Tabula CLI/CI operator (${var.enclave_name})"
  description  = "Tabula CI/CLI operator SA (enclave: ${var.enclave_name}). No roles granted by this module; impersonation set up out-of-band. See terraform/modules/iam/README.md."
}

###############################################################################
# Custom role: classifier -> GPU wake signal
#
# Strictly tighter than roles/compute.instanceAdmin.v1. Only what's needed for
# the bootstrap wake-signal mechanism (#37). Bound below with an IAM condition
# scoped to var.gpu_instance_id when known.
###############################################################################

resource "google_project_iam_custom_role" "gpu_waker" {
  project     = var.project_id
  role_id     = local.gpu_waker_role_id
  title       = "Tabula GPU waker (${var.enclave_name})"
  description = "Allows starting and reading metadata of a single GPU VM. Granted to the classifier SA so it can wake the GPU on demand. Always paired with an IAM condition that pins the binding to one specific instance."
  permissions = [
    "compute.instances.get",
    "compute.instances.start",
  ]
  stage = "GA"
}

###############################################################################
# Classifier SA bindings
###############################################################################

resource "google_project_iam_member" "classifier_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.classifier.email}"
}

resource "google_project_iam_member" "classifier_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.classifier.email}"
}

# Conditional binding of the gpu_waker custom role, scoped to a single GPU
# instance. Only created when var.gpu_instance_id is supplied.
resource "google_project_iam_member" "classifier_gpu_waker" {
  count   = local.bind_classifier_to_gpu ? 1 : 0
  project = var.project_id
  role    = google_project_iam_custom_role.gpu_waker.id
  member  = "serviceAccount:${google_service_account.classifier.email}"

  condition {
    title       = "Pin to single GPU instance"
    description = "Restricts the classifier wake permission to exactly one GPU instance: ${var.gpu_instance_id}"
    expression  = "resource.name == \"${var.gpu_instance_id}\""
  }
}

###############################################################################
# GPU SA bindings
#
# Vertex AI access plus log/metric write — nothing else. In particular:
#   * no roles/storage.*       (GPU must not exfil to GCS)
#   * no roles/iam.*           (GPU must not mutate IAM)
#   * no roles/compute.*       (beyond the implicit instance-metadata read
#                               GCE grants from the metadata server itself —
#                               that's not a project IAM binding)
###############################################################################

resource "google_project_iam_member" "gpu_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.gpu.email}"
}

resource "google_project_iam_member" "gpu_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.gpu.email}"
}

resource "google_project_iam_member" "gpu_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.gpu.email}"
}

###############################################################################
# Gitea SA bindings
###############################################################################

resource "google_project_iam_member" "gitea_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.gitea.email}"
}

resource "google_project_iam_member" "gitea_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.gitea.email}"
}
