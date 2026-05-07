variable "project_id" {
  description = "GCP project ID hosting the enclave."
  type        = string
}

variable "region" {
  description = "GCP region (must match the VPC subnet's region)."
  type        = string
}

variable "zone" {
  description = "GCP zone within `region` for the classifier VM."
  type        = string
}

variable "name" {
  description = "Instance name. Must be RFC1035-compliant. Defaults to `tabula-classifier`."
  type        = string
  default     = "tabula-classifier"
}

variable "machine_type" {
  description = <<-EOT
    GCE machine type for the classifier VM. Default `e2-medium` gives 2 vCPU /
    4 GB RAM, comfortable headroom for `llama3.2:1b` (~1.3 GB) and the Noise
    terminator. `e2-small` (2 vCPU / 2 GB RAM) is a cheaper option that still
    fits the default model but leaves little room for the Noise stack.
  EOT
  type        = string
  default     = "e2-medium"
}

variable "disk_size_gb" {
  description = "Boot disk size in GB. Default 30 GB fits OS + one small Ollama model with headroom."
  type        = number
  default     = 30
}

variable "disk_type" {
  description = "Boot disk type. `pd-balanced` is the cost/perf default for always-on infra."
  type        = string
  default     = "pd-balanced"
}

variable "image_family" {
  description = <<-EOT
    Source image family for the boot disk. The repo standardises on
    `debian-12` across all enclave VMs (classifier / GPU / Gitea) for
    consistency, smaller image, fast boot, mature cloud-init.
  EOT
  type        = string
  default     = "debian-12"
}

variable "image_project" {
  description = "GCP project that hosts the source image family."
  type        = string
  default     = "debian-cloud"
}

variable "subnet_id" {
  description = "Self-link or ID of the private subnet from the VPC module. Must be in `region`."
  type        = string
}

variable "service_account_email" {
  description = "Email of the pre-provisioned classifier service account (from the IAM module)."
  type        = string
}

variable "service_account_scopes" {
  description = <<-EOT
    OAuth scopes attached to the VM. `cloud-platform` is the recommended
    default; the underlying SA's IAM bindings still gate what actions are
    permitted (least-privilege is enforced in the IAM module, not here).
  EOT
  type        = list(string)
  default     = ["https://www.googleapis.com/auth/cloud-platform"]
}

variable "model_name" {
  description = <<-EOT
    Ollama model tag pulled at first boot. Default `llama3.2:1b` is small
    (~1.3 GB), fast, and adequate for haiku-class routing. Alternative:
    `qwen2.5:0.5b` (~400 MB) for tighter footprint.
  EOT
  type        = string
  default     = "llama3.2:1b"
}

variable "gpu_instance_name" {
  description = <<-EOT
    Name of the GPU VM that the wake-signal script (future issue) will
    target. Wired into the `/opt/tabula/wake-gpu.sh` placeholder so the
    follow-up implementation has a known target. Pass an empty string if
    the GPU VM does not yet exist.
  EOT
  type        = string
  default     = ""
}

variable "gpu_instance_zone" {
  description = "Zone of the GPU VM (paired with `gpu_instance_name`). Empty string if unset."
  type        = string
  default     = ""
}

variable "network_tags" {
  description = <<-EOT
    Network tags applied to the VM. Required tags `enclave-workload` and
    `enclave-classifier` are appended automatically; any tags listed here
    are unioned with them.
  EOT
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Resource labels applied to the instance."
  type        = map(string)
  default = {
    component = "classifier"
    layer     = "ingress"
    managed   = "terraform"
  }
}

variable "deletion_protection" {
  description = "Whether to protect the instance from deletion. Off by default — enclaves are disposable."
  type        = bool
  default     = false
}

variable "metadata" {
  description = "Additional GCE metadata key/value pairs to merge with the module-managed metadata."
  type        = map(string)
  default     = {}
}
