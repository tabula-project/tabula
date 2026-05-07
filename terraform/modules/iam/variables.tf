variable "project_id" {
  description = "GCP project that owns the enclave. All service accounts and IAM bindings created by this module are scoped to this project. Per the parent epic (#12) one enclave maps to exactly one GCP project; cross-project IAM is explicitly out of scope."
  type        = string

  validation {
    condition     = length(var.project_id) > 0
    error_message = "project_id must be a non-empty GCP project ID."
  }
}

variable "enclave_name" {
  description = "Short, stable identifier for this enclave (e.g., \"prod\", \"dev\", \"alice-1\"). Used to namespace service account IDs, display names, and the custom IAM role so multiple enclaves in the same GCP organization (or, accidentally, the same project) cannot collide. Must be DNS-label compatible: lowercase letters, digits, and hyphens; 1-30 characters."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{0,28}[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be 1-30 characters, lowercase alphanumeric and hyphens, and must start with a letter and end with a letter or digit."
  }
}

variable "gpu_instance_id" {
  description = "Optional fully qualified instance ID of the GPU VM the classifier is allowed to wake (start). When set, the classifier SA receives the custom \"gpu_waker\" role conditioned to this single instance via an IAM condition. When null (default), the wake binding is skipped here and is expected to be created by the GPU VM module via a separate google_project_iam_member to avoid a chicken-and-egg between IAM and the GPU VM module. Format: \"projects/<project>/zones/<zone>/instances/<instance-name>\"."
  type        = string
  default     = null
}

variable "create_cli_operator_sa" {
  description = "When true, create an additional service account intended for human-operator CLI / CI use (e.g., running terraform plan/apply from a controlled CI runner). This SA is created with no IAM bindings of its own; the operator workflow grants impersonation rights out-of-band. Default: false (operators are typically authenticated as Google identities, not as a service account)."
  type        = bool
  default     = false
}
