variable "project_id" {
  description = "GCP project ID hosting the enclave VPC and firewall rules."
  type        = string
}

variable "network" {
  description = "VPC network self-link or name (output of the VPC module, e.g. module.vpc.network_self_link). All firewall rules attach to this network."
  type        = string
}

variable "noise_port" {
  description = "TCP port the classifier VM listens on for the Noise XX handshake. This is the only port reachable from 0.0.0.0/0."
  type        = number
  default     = 7000

  validation {
    condition     = var.noise_port > 0 && var.noise_port < 65536
    error_message = "noise_port must be a valid TCP port (1-65535)."
  }
}

variable "wake_signal_port" {
  description = "TCP port on the GPU VM that the classifier hits to wake it (start-on-demand wake-signal mechanism). Internal-only; allowed from classifier to GPU."
  type        = number
  default     = 8088

  validation {
    condition     = var.wake_signal_port > 0 && var.wake_signal_port < 65536
    error_message = "wake_signal_port must be a valid TCP port (1-65535)."
  }
}

variable "gitea_port" {
  description = "TCP port Gitea serves HTTP on. Internal-only; allowed from GPU to Gitea."
  type        = number
  default     = 3000

  validation {
    condition     = var.gitea_port > 0 && var.gitea_port < 65536
    error_message = "gitea_port must be a valid TCP port (1-65535)."
  }
}

variable "enable_iap_ssh" {
  description = "If true, allow SSH (TCP/22) from the IAP CIDR (35.235.240.0/20) to anything tagged enclave-workload. Operator SSH path. Set to false to disable all SSH ingress."
  type        = bool
  default     = true
}

variable "classifier_tag" {
  description = "Network tag set on the classifier VM. Used as target for the external Noise allow rule."
  type        = string
  default     = "enclave-classifier"
}

variable "gpu_tag" {
  description = "Network tag set on the GPU VM. Used as target for the classifier->GPU wake-signal rule."
  type        = string
  default     = "enclave-gpu"
}

variable "gitea_tag" {
  description = "Network tag set on the Gitea VM. Used as target for the GPU->Gitea rule."
  type        = string
  default     = "enclave-gitea"
}

variable "workload_tag" {
  description = "Umbrella network tag set on every enclave VM (classifier, GPU, Gitea). Used as the target for IAP-SSH."
  type        = string
  default     = "enclave-workload"
}

variable "name_prefix" {
  description = "Prefix prepended to every firewall rule name. Useful for namespacing rules across multiple enclaves in the same project."
  type        = string
  default     = "enclave"
}
