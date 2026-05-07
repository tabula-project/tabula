###############################################################################
# Tabula enclave Gitea module — input variables.
###############################################################################

variable "project_id" {
  description = "GCP project ID hosting the enclave."
  type        = string

  validation {
    condition     = length(var.project_id) > 0
    error_message = "project_id must be a non-empty GCP project ID."
  }
}

variable "region" {
  description = "GCP region for the Gitea VM, data disk, and DNS resources. Must match the network module's region."
  type        = string
}

variable "zone" {
  description = "GCP zone within var.region for the Gitea VM and persistent data disk."
  type        = string
}

variable "enclave_name" {
  description = "Short name used to prefix resource names and form the internal DNS suffix (e.g., 'prod', 'dev01'). The Gitea VM is reachable at gitea.<enclave_name>.internal."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{0,30}[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be a valid GCE name fragment: lowercase letters, digits, and hyphens, 1-32 chars, starting with a letter."
  }
}

# ----- Network inputs (from #14 network module outputs) -----

variable "network_self_link" {
  description = "Self-link of the VPC network (network module output: vpc_self_link). Used to attach the private DNS zone."
  type        = string
}

variable "subnet_self_link" {
  description = "Self-link of the workload subnet (network module output: subnet_self_link)."
  type        = string
}

# ----- IAM inputs (from #15 IAM module outputs) -----

variable "gitea_sa_email" {
  description = "Email of the dedicated Gitea service account (IAM module output: gitea_sa_email). The SA needs read access to any Secret Manager entries used to seed the admin password."
  type        = string
}

# ----- Machine shape -----

variable "machine_type" {
  description = "GCE machine type for the Gitea host. e2-small is sufficient for the small-team usage profile inside an enclave; bump to e2-medium if repo activity grows."
  type        = string
  default     = "e2-small"
}

# ----- Boot disk -----

variable "image_family" {
  description = "Source image family for the boot disk. Cloud-init expects a Debian/Ubuntu-family image."
  type        = string
  default     = "ubuntu-2204-lts"
}

variable "image_project" {
  description = "Project that owns the source image family."
  type        = string
  default     = "ubuntu-os-cloud"
}

variable "boot_disk_size_gb" {
  description = "Boot disk size in GB. 20 GB is enough for the OS and the Gitea binary; repo data lives on the separate persistent data disk."
  type        = number
  default     = 20
}

variable "boot_disk_type" {
  description = "Boot disk type."
  type        = string
  default     = "pd-balanced"
}

# ----- Persistent data disk -----

variable "data_disk_size_gb" {
  description = "Persistent data disk size in GB, mounted at /var/lib/gitea. 50 GB default holds substantial repo history for a small team. Disk is created in this module with auto_delete = false on the attachment so it survives in-place VM rebuilds."
  type        = number
  default     = 50
}

variable "data_disk_type" {
  description = "Persistent data disk type. pd-balanced is appropriate for git workloads (mixed sequential/random)."
  type        = string
  default     = "pd-balanced"
}

# ----- Gitea version -----

variable "gitea_version" {
  description = "Gitea binary version to install. Pin to a specific patch release (e.g., '1.22.3') to make installs reproducible. Cloud-init fetches https://dl.gitea.com/gitea/<version>/gitea-<version>-linux-amd64; the Cloud NAT egress allowlist (#14) must permit dl.gitea.com or a configured GCS mirror."
  type        = string
  default     = "1.22.3"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.gitea_version))
    error_message = "gitea_version must be a full semver (e.g., '1.22.3'), not a prefix like '1.22'."
  }
}

# ----- Network tags -----

variable "extra_network_tags" {
  description = "Additional GCE network tags to attach to the instance. The module always applies enclave-workload (egress allowlist from #14) and enclave-gitea (ingress firewall keys off this in #20/#24)."
  type        = list(string)
  default     = []
}

# ----- Labels -----

variable "labels" {
  description = "Resource labels applied to the instance, data disk, and DNS zone."
  type        = map(string)
  default     = {}
}

# ----- Cloud-init overrides -----

variable "cloud_init_extra_user_data" {
  description = "Optional extra cloud-init user-data fragment appended after the module's runcmd block. Use sparingly; most customization belongs in a dedicated module wrapper."
  type        = string
  default     = ""
}
