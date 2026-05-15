variable "project_id" {
  description = "GCP project ID hosting the enclave."
  type        = string
}

variable "region" {
  description = "GCP region. Must match the network module's region and have T4 (or chosen GPU) quota."
  type        = string
}

variable "zone" {
  description = "GCP zone within var.region. Must offer the chosen accelerator type."
  type        = string
}

variable "enclave_name" {
  description = "Enclave name used for resource naming. Mirrors var.enclave_name in sibling modules."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]*[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be a valid GCP resource name fragment (lowercase letters, digits, hyphens; must start with a letter)."
  }
}

# ----- Network inputs (from #14 network module outputs) -----

variable "network_self_link" {
  description = "Self-link of the VPC network (network module output: vpc_self_link)."
  type        = string
}

variable "subnet_self_link" {
  description = "Self-link of the workload subnet (network module output: subnet_self_link)."
  type        = string
}

# ----- IAM inputs (from #15 IAM module outputs) -----

variable "gpu_sa_email" {
  description = "Email of the dedicated GPU service account (IAM module output: gpu_sa_email). Must already hold roles/aiplatform.user (asserted in #15, not re-declared here)."
  type        = string
}

# ----- Machine shape -----

variable "machine_type" {
  description = "GCE machine type for the GPU host. n1-* family is required to attach Tesla T4 accelerators."
  type        = string
  default     = "n1-standard-4"
}

variable "accelerator_type" {
  description = "GPU accelerator type. Default nvidia-tesla-t4 is the cheapest viable shape for the demo. Override (e.g. nvidia-tesla-l4) requires a machine_type that supports it."
  type        = string
  default     = "nvidia-tesla-t4"
}

variable "accelerator_count" {
  description = "Number of GPUs attached to the instance. Default 1 (single T4) is sufficient for the claude CLI demo workload."
  type        = number
  default     = 1

  validation {
    condition     = var.accelerator_count >= 1 && var.accelerator_count <= 8
    error_message = "accelerator_count must be between 1 and 8."
  }
}

# ----- Boot disk -----

variable "image_family" {
  description = "Source image family for the boot disk. Bootstrap (separate issue) installs claude CLI on top."
  type        = string
  default     = "ubuntu-2204-lts"
}

variable "image_project" {
  description = "Project that owns the source image family."
  type        = string
  default     = "ubuntu-os-cloud"
}

variable "boot_disk_size_gb" {
  description = "Boot disk size in GB. 100 GB default leaves room for a substrate git clone, model caches, and apt state."
  type        = number
  default     = 100
}

variable "boot_disk_type" {
  description = "Boot disk type. pd-balanced is a good cost/perf trade for the bootstrap workload."
  type        = string
  default     = "pd-balanced"
}

# ----- Sleep policy -----

variable "stop_schedule_cron" {
  description = "Cron expression for the unconditional stop schedule. Default '0 * * * *' fires hourly; the wake signal (separate issue) restarts the VM on demand. NOTE: this can kill in-flight requests — see README. GCP requires schedules ≥ 1 hour apart (consecutive fires must be ≥ 60min); '0 * * * *' is the most aggressive allowed."
  type        = string
  default     = "0 * * * *"
}

variable "stop_schedule_timezone" {
  description = "IANA timezone for the stop schedule. UTC is recommended to keep operator reasoning simple."
  type        = string
  default     = "UTC"
}

# ----- Tags / labels -----

variable "extra_network_tags" {
  description = "Additional GCE network tags to attach to the instance (e.g., for ad-hoc firewall composition). The module always applies enclave-workload and enclave-gpu."
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Resource labels applied to the instance and resource policy."
  type        = map(string)
  default     = {}
}

# ----- Startup script (placeholder; bootstrap issue owns content) -----

variable "startup_script" {
  description = "Optional startup script body. The Bootstrap issue will fill this in; default is empty so this module stays narrowly scoped."
  type        = string
  default     = ""
}
