# Production composition variables.
#
# Mirrors `terraform/enclave/variables.tf` (project_id, region, enclave_name)
# and adds the inputs the real sibling modules require which the stubs didn't
# need (zone for VMs, noise_port for firewall/output echo). Defaults are
# chosen to match the original epic #12 design choices: us-central1-a (T4 GPU
# availability), TCP 7000 for the public Noise port (firewall module default).

variable "project_id" {
  description = "GCP project ID that owns the enclave's resources."
  type        = string
}

variable "region" {
  description = "GCP region to place the enclave in (e.g. us-central1)."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone within `region` for VM-bearing modules (classifier, gpu, gitea). Must offer Tesla T4 quota."
  type        = string
  default     = "us-central1-a"
}

variable "enclave_name" {
  description = "DNS-safe enclave name, 3-30 chars, lowercase, validated by the CLI."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{1,28}[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be DNS-safe: lowercase, start with a letter, end with a letter or digit, 3-30 chars, hyphens allowed in the middle."
  }
}

variable "noise_port" {
  description = "TCP port the classifier VM listens on for the Noise XX handshake. The only port reachable from 0.0.0.0/0."
  type        = number
  default     = 7000

  validation {
    condition     = var.noise_port > 0 && var.noise_port < 65536
    error_message = "noise_port must be a valid TCP port (1-65535)."
  }
}

# ----- GPU host shape (#120) -----
#
# These pass through to the gpu module's `accelerator_type` and `machine_type`
# variables; defaults preserve the original epic #12 choice (single Tesla T4
# on n1-standard-4) for back-compat. Override at composition root when the
# default zone hits T4 capacity exhaustion.

variable "gpu_accelerator_type" {
  description = <<-EOT
    GPU accelerator type for the classifier-to-Vertex inference host. Default
    `nvidia-tesla-t4` is the cheapest viable shape for the chat-enclave workload.
    `nvidia-l4` is the recommended fallback when T4 capacity is exhausted (often
    available when T4 is not), but L4 attaches only to the `g2-*` machine
    family — pair this with `gpu_machine_type = "g2-standard-4"` (or larger).
    Passed through to module.gpu.accelerator_type.
  EOT
  type        = string
  default     = "nvidia-tesla-t4"
}

variable "gpu_machine_type" {
  description = <<-EOT
    GCE machine type for the GPU host. Must be compatible with
    `gpu_accelerator_type`: `n1-*` family (e.g. `n1-standard-4`) for Tesla T4,
    `g2-*` family (e.g. `g2-standard-4`) for L4. Default `n1-standard-4` pairs
    with the default T4. Passed through to module.gpu.machine_type.
  EOT
  type        = string
  default     = "n1-standard-4"
}
