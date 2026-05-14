# Bootstrap wiring for the GPU VM — issue #35.
#
# This file is the contract between the bootstrap script (bootstrap.sh) and
# the GCE instance (defined in main.tf, owned by issue #19). It does NOT
# create any GCP resources by itself; it produces two locals that main.tf
# reads:
#
#   - local.tabula_bootstrap_user_data
#       The rendered cloud-init body. Attach as instance metadata under the
#       key `user-data` so cloud-init picks it up:
#           metadata = merge(local.tabula_bootstrap_metadata, {
#             user-data = local.tabula_bootstrap_user_data
#           })
#
#   - local.tabula_bootstrap_metadata
#       The instance metadata attributes that bootstrap.sh reads at runtime
#       via the GCE metadata service. Each key is fetched in bootstrap.sh
#       under /computeMetadata/v1/instance/attributes/<key>.
#
# The split keeps this issue narrowly scoped: the GPU module from #19 owns
# the google_compute_instance resource and only needs a one-line `merge` to
# adopt this bootstrap.

# ---------------------------------------------------------------------------
# Bootstrap-specific inputs. `enclave_name` is declared in this module's
# variables.tf (issue #19); these are the additional inputs the bootstrap
# wiring needs and which don't belong on the bare GCE instance config.
# ---------------------------------------------------------------------------

# The bootstrap variables below are OPTIONAL — they default to "" so a
# composition that doesn't yet wire the bootstrap mechanism into the GCE
# instance metadata can still pass `terraform validate`. Validations are
# permissive when empty (the bootstrap mechanism is opt-in; an empty value
# means "no bootstrap wired yet").
#
# When main.tf is updated to merge `local.tabula_bootstrap_metadata` and
# attach `local.tabula_bootstrap_user_data` to the instance's user-data,
# tighten the validations here to reject the empty default.

variable "vertex_project_id" {
  description = "GCP project hosting Vertex AI. Read by bootstrap.sh; passed to claude as ANTHROPIC_VERTEX_PROJECT_ID. Empty means bootstrap is not wired."
  type        = string
  default     = ""
}

variable "vertex_region" {
  description = "Vertex AI region. Read by bootstrap.sh; passed to claude as CLOUD_ML_REGION. Must be a region where the chosen model is available and where the PSC endpoint (#23) is provisioned. Empty means bootstrap is not wired."
  type        = string
  default     = ""
}

variable "gitea_url" {
  description = "Base URL of the in-enclave Gitea (issue #21). Example: https://gitea.<enclave>.internal:3000. Empty means bootstrap is not wired."
  type        = string
  default     = ""

  validation {
    condition     = var.gitea_url == "" || can(regex("^https?://[^/]+/?$", var.gitea_url))
    error_message = "gitea_url must be empty or of the form https://host[:port] with no trailing path."
  }
}

variable "gitea_repo_path" {
  description = "Owner/repo path inside Gitea, without leading slash and without .git suffix. Example: tabula/tabula. Empty means bootstrap is not wired."
  type        = string
  default     = ""

  validation {
    condition     = var.gitea_repo_path == "" || can(regex("^[^/]+/[^/]+$", var.gitea_repo_path))
    error_message = "gitea_repo_path must be empty or of the form '<owner>/<repo>'."
  }
}

variable "gitea_token_secret" {
  description = "Secret Manager secret id (just the short name, e.g. 'gitea-bootstrap-pat') holding a Gitea PAT with read access to the repo. The IAM module (#15) MUST grant the GPU SA roles/secretmanager.secretAccessor scoped to this single secret. Empty means bootstrap is not wired."
  type        = string
  default     = ""
}

variable "claude_version" {
  description = "Pinned claude CLI version. The bootstrap script verifies the installed binary reports this exact version and refuses to start the agent if it does not match. Empty means bootstrap is not wired."
  type        = string
  default     = ""
}

variable "git_user_email" {
  description = "Service identity for git user.email. Convention: tabula-enclave@<enclave>.internal. Empty means bootstrap is not wired."
  type        = string
  default     = ""

  validation {
    condition     = var.git_user_email == "" || can(regex("^[^@]+@[^@]+$", var.git_user_email))
    error_message = "git_user_email must be empty or look like an email address."
  }
}

variable "agent_driver_socket" {
  description = "Filesystem path to the Unix domain socket the tabula-agent.service exposes for the claude driver (#22). Defaults to /run/tabula/agent.sock."
  type        = string
  default     = "/run/tabula/agent.sock"
}

# ---------------------------------------------------------------------------
# Renders.
# ---------------------------------------------------------------------------

locals {
  # The bootstrap script itself is plain bash with no Terraform template
  # substitutions — it reads its config from instance metadata at runtime.
  # We just embed the file contents into the cloud-init `write_files` block.
  tabula_bootstrap_script_body = file("${path.module}/bootstrap.sh")

  tabula_bootstrap_user_data = templatefile(
    "${path.module}/cloud-init.yaml.tftpl",
    {
      bootstrap_script = local.tabula_bootstrap_script_body
    },
  )

  # Metadata attributes consumed by bootstrap.sh. Keep keys in sync with the
  # `metadata_required` calls at the top of bootstrap.sh.
  tabula_bootstrap_metadata = {
    "tabula-enclave-name"       = var.enclave_name
    "tabula-vertex-project"     = var.vertex_project_id
    "tabula-vertex-region"      = var.vertex_region
    "tabula-gitea-url"          = var.gitea_url
    "tabula-gitea-repo"         = var.gitea_repo_path
    "tabula-gitea-token-secret" = var.gitea_token_secret
    "tabula-claude-version"     = var.claude_version
    "tabula-git-user-email"     = var.git_user_email
    "tabula-agent-driver-sock"  = var.agent_driver_socket
  }
}

# ---------------------------------------------------------------------------
# Outputs (handy for callers that want to inspect the bootstrap from the
# enclave root module or for operators debugging via `terraform output`).
# ---------------------------------------------------------------------------

output "bootstrap_user_data" {
  description = "Rendered cloud-init body that the GPU instance should attach as the `user-data` metadata key."
  value       = local.tabula_bootstrap_user_data
  sensitive   = true # contains the embedded bootstrap script
}

output "bootstrap_metadata" {
  description = "Instance metadata attributes consumed by bootstrap.sh at runtime."
  value       = local.tabula_bootstrap_metadata
}
