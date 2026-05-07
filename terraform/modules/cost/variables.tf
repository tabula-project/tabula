variable "enclave_name" {
  description = "Logical name of the enclave (used for budget display name, topic names, function names, and the state.json marker key)."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,38}[a-z0-9]$", var.enclave_name))
    error_message = "enclave_name must be 3-40 chars, lowercase, start with a letter, and contain only [a-z0-9-]."
  }
}

variable "project_id" {
  description = "GCP project that scopes this enclave (one enclave = one project)."
  type        = string
}

variable "region" {
  description = "GCP region for regional resources (Pub/Sub topic remains global; Cloud Function and Cloud Storage are regional)."
  type        = string
  default     = "us-central1"
}

variable "billing_account_id" {
  description = "Billing account ID (e.g. 'XXXXXX-XXXXXX-XXXXXX'). Required by google_billing_budget. Never hardcode — always pass via tfvars or env."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly budget cap, USD. Default is intentionally small (USD 50) to fail safe — operators must opt-in to larger budgets explicitly."
  type        = number
  default     = 50
  validation {
    condition     = var.monthly_budget_usd > 0 && var.monthly_budget_usd <= 100000
    error_message = "monthly_budget_usd must be > 0 and <= 100000 (sanity ceiling — increase the validation if you really need more)."
  }
}

variable "alert_email_addresses" {
  description = "Email addresses that receive budget threshold alerts (50/90/100%). Each address gets a google_monitoring_notification_channel."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for e in var.alert_email_addresses : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", e))])
    error_message = "alert_email_addresses entries must look like email addresses."
  }
}

variable "workload_label_key" {
  description = "GCE instance label key used to identify enclave workload VMs that the auto-kill function may stop."
  type        = string
  default     = "enclave-workload"
}

variable "workload_label_value" {
  description = "GCE instance label value (combined with workload_label_key) that scopes the auto-kill blast radius. Default 'true' matches `labels = { enclave-workload = \"true\" }`."
  type        = string
  default     = "true"
}

variable "auto_kill_enabled" {
  description = "Master switch for the auto-stop Cloud Function. Set to false for long-running operator sessions where you have manually approved a higher cost. The budget + alerts still fire; only the automatic VM stop is suppressed."
  type        = bool
  default     = true
}

variable "state_bucket_name" {
  description = "Name of an existing Cloud Storage bucket where the auto-kill function writes the cost_killed marker for this enclave. The function writes object 'enclaves/<enclave_name>/state.json'. If empty, the module creates a dedicated bucket."
  type        = string
  default     = ""
}

variable "function_runtime" {
  description = "Cloud Functions Python runtime."
  type        = string
  default     = "python311"
}

variable "kill_threshold_percent" {
  description = "Spend percentage at which the auto-kill function actually stops VMs. 100 means 'kill at 100% of budget'. Lower thresholds (e.g. 90) stop earlier; >100 lets the project blow past budget before stopping. Threshold rules at 50/90/100% always emit notifications regardless."
  type        = number
  default     = 100
  validation {
    condition     = var.kill_threshold_percent >= 50 && var.kill_threshold_percent <= 200
    error_message = "kill_threshold_percent must be between 50 and 200."
  }
}
