###############################################################################
# Cost guardrails module
#
# Layers:
#   1. google_billing_budget (project-level, 50/90/100% thresholds)
#   2. google_pubsub_topic    (budget notifications)
#   3. google_monitoring_notification_channel (one per email)
#   4. google_cloudfunctions2_function (auto-kill: stops enclave-workload VMs)
#   5. google_storage_bucket  (state.json marker storage; created if not provided)
#
# Hard rule: this module STOPS VMs. It MUST NOT issue any *.delete or
# *.destroy API calls. Destruction is gated behind `tabula enclave down` (#28).
###############################################################################

locals {
  # Single source of truth for resource naming. Keeps things consistent
  # whether or not the operator passed an external state bucket.
  name_prefix = "tabula-${var.enclave_name}"

  # If the caller supplied a bucket, we trust it. Otherwise the module
  # provisions one with uniform_bucket_level_access enabled.
  state_bucket_managed = var.state_bucket_name == ""
  state_bucket_name    = local.state_bucket_managed ? google_storage_bucket.state[0].name : var.state_bucket_name

  # Budget threshold rules. We always emit notifications at 50/90/100;
  # the kill threshold (which actually stops VMs) is enforced inside the
  # Cloud Function (var.kill_threshold_percent) so operators can adjust it
  # without re-deploying the budget rules.
  budget_thresholds = [0.5, 0.9, 1.0]
}

###############################################################################
# State bucket (optional — only created when caller does not provide one)
###############################################################################

resource "google_storage_bucket" "state" {
  count = local.state_bucket_managed ? 1 : 0

  project                     = var.project_id
  name                        = "${local.name_prefix}-cost-state"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    # Versioning is intentional: we never want to silently lose a cost_killed
    # marker if a later run rewrites state.json.
    enabled = true
  }

  labels = {
    enclave   = var.enclave_name
    component = "cost"
  }
}

###############################################################################
# Pub/Sub topic for budget notifications
###############################################################################

resource "google_pubsub_topic" "budget_alerts" {
  project = var.project_id
  name    = "${local.name_prefix}-budget-alerts"

  labels = {
    enclave   = var.enclave_name
    component = "cost"
  }
}

# The Cloud Billing service account must be able to publish to the topic.
# Identity is fixed by GCP: cloud-billing-budget-notifier@system.gserviceaccount.com
resource "google_pubsub_topic_iam_member" "billing_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.budget_alerts.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:cloud-billing-budget-notifier@system.gserviceaccount.com"
}

###############################################################################
# Email notification channels (one per address)
###############################################################################

resource "google_monitoring_notification_channel" "email" {
  for_each = toset(var.alert_email_addresses)

  project      = var.project_id
  display_name = "tabula-${var.enclave_name}-budget-${replace(each.value, "@", "-at-")}"
  type         = "email"
  labels = {
    email_address = each.value
  }
  enabled = true
}

###############################################################################
# Billing budget
###############################################################################

resource "google_billing_budget" "enclave" {
  billing_account = var.billing_account_id
  display_name    = "tabula-${var.enclave_name} (project ${var.project_id})"

  budget_filter {
    projects = ["projects/${var.project_id}"]
    # Project-level only. Billing-account-wide budgets are explicitly out of scope.
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(floor(var.monthly_budget_usd))
    }
  }

  dynamic "threshold_rules" {
    for_each = local.budget_thresholds
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  all_updates_rule {
    pubsub_topic   = google_pubsub_topic.budget_alerts.id
    schema_version = "1.0"
    monitoring_notification_channels = [
      for c in google_monitoring_notification_channel.email : c.id
    ]
    # disable_default_iam_recipients defaults to false; billing admins also
    # receive alerts by default. We don't override it.
  }

  depends_on = [
    google_pubsub_topic_iam_member.billing_publisher,
  ]
}

###############################################################################
# Cloud Function service account + IAM
#
# Tightly scoped: this SA can only stop instances and write the marker object.
# It cannot delete, modify configuration, or touch any other project resource.
###############################################################################

resource "google_service_account" "auto_kill" {
  project      = var.project_id
  account_id   = "${substr(replace(var.enclave_name, "_", "-"), 0, 22)}-cost-kill"
  display_name = "tabula ${var.enclave_name} cost auto-kill"
  description  = "Stops enclave-workload VMs when the project budget is exceeded. Cannot delete resources."
}

# Custom role: only compute.instances.stop and the read perms required to
# enumerate the workload VMs. Coordinated with IAM module (#15) — defined here
# because the role is meaningless outside this module's lifetime.
resource "google_project_iam_custom_role" "auto_kill" {
  project     = var.project_id
  role_id     = "tabulaCostAutoKill_${replace(var.enclave_name, "-", "_")}"
  title       = "Tabula cost auto-kill (${var.enclave_name})"
  description = "Allows stopping enclave-workload VMs only. No delete, no modify, no destroy."
  stage       = "GA"
  permissions = [
    "compute.instances.stop",
    "compute.instances.list",
    "compute.instances.get",
    "compute.zones.list",
  ]
}

resource "google_project_iam_member" "auto_kill_custom" {
  project = var.project_id
  role    = google_project_iam_custom_role.auto_kill.id
  member  = "serviceAccount:${google_service_account.auto_kill.email}"
}

# Marker writer: only objectCreator on the dedicated state bucket.
resource "google_storage_bucket_iam_member" "auto_kill_state_writer" {
  bucket = local.state_bucket_name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.auto_kill.email}"
}

###############################################################################
# Cloud Function source — packaged from local auto_kill/ directory
###############################################################################

data "archive_file" "auto_kill_source" {
  type        = "zip"
  source_dir  = "${path.module}/auto_kill"
  output_path = "${path.module}/.terraform-build/auto_kill.zip"
  excludes = [
    "__pycache__",
    "*.pyc",
    "tests",
    "test_main.py",
  ]
}

resource "google_storage_bucket" "function_source" {
  project                     = var.project_id
  name                        = "${local.name_prefix}-cost-fn-src"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  labels = {
    enclave   = var.enclave_name
    component = "cost"
  }
}

resource "google_storage_bucket_object" "auto_kill_source" {
  # Hash the zip contents so a code change forces a redeploy.
  name   = "auto_kill-${data.archive_file.auto_kill_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.auto_kill_source.output_path
}

###############################################################################
# Cloud Function — Pub/Sub triggered
###############################################################################

resource "google_cloudfunctions2_function" "auto_kill" {
  project  = var.project_id
  name     = "${local.name_prefix}-cost-auto-kill"
  location = var.region

  build_config {
    runtime     = var.function_runtime
    entry_point = "handle_budget_event"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.auto_kill_source.name
      }
    }
  }

  service_config {
    max_instance_count    = 3
    min_instance_count    = 0
    available_memory      = "256M"
    timeout_seconds       = 120
    service_account_email = google_service_account.auto_kill.email

    environment_variables = {
      ENCLAVE_NAME           = var.enclave_name
      PROJECT_ID             = var.project_id
      WORKLOAD_LABEL_KEY     = var.workload_label_key
      WORKLOAD_LABEL_VALUE   = var.workload_label_value
      AUTO_KILL_ENABLED      = tostring(var.auto_kill_enabled)
      STATE_BUCKET           = local.state_bucket_name
      KILL_THRESHOLD_PERCENT = tostring(var.kill_threshold_percent)
    }
  }

  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic   = google_pubsub_topic.budget_alerts.id
    retry_policy   = "RETRY_POLICY_RETRY"
  }

  depends_on = [
    google_project_iam_member.auto_kill_custom,
    google_storage_bucket_iam_member.auto_kill_state_writer,
  ]
}
