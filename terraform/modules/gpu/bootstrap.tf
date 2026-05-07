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
# Inputs (declared here so this issue can be reviewed in isolation; once the
# wiring is merged into the GPU module, these can move to variables.tf next
# to the other module inputs).
#
# NOTE on `enclave_name`: that variable is also declared by issue #19's
# variables.tf. Until #19 merges it does not exist in this directory, which
# would make `terraform validate` fail. The block below is therefore guarded
# with a comment so the merge conflict resolution is explicit: when both PRs
# are on main, drop this declaration here and keep the one in variables.tf.
# ---------------------------------------------------------------------------

variable "enclave_name" {
  # SHIM — duplicates issue #19's declaration. See note above. The schema
  # MUST stay identical to variables.tf in #19; if that PR's schema changes,
  # update this shim or remove it on merge.
  description = "Enclave name used for resource naming. Mirrors var.enclave_name from issue #19."
  type        = string

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]*[a-z0-9])?$", var.enclave_name))
    error_message = "enclave_name must be a valid GCP resource name fragment (lowercase letters, digits, hyphens; must start with a letter)."
  }
}

variable "vertex_project_id" {
  description = "GCP project hosting Vertex AI. Read by bootstrap.sh; passed to claude as ANTHROPIC_VERTEX_PROJECT_ID."
  type        = string
}

variable "vertex_region" {
  description = "Vertex AI region. Read by bootstrap.sh; passed to claude as CLOUD_ML_REGION. Must be a region where the chosen model is available and where the PSC endpoint (#23) is provisioned."
  type        = string
}

variable "gitea_url" {
  description = "Base URL of the in-enclave Gitea (issue #21). Example: https://gitea.<enclave>.internal:3000. The VM must be able to reach this host on the enclave VPC."
  type        = string

  validation {
    condition     = can(regex("^https?://[^/]+/?$", var.gitea_url))
    error_message = "gitea_url must be of the form https://host[:port] with no trailing path."
  }
}

variable "gitea_repo_path" {
  description = "Owner/repo path inside Gitea, without leading slash and without .git suffix. Example: tabula/tabula."
  type        = string

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.gitea_repo_path))
    error_message = "gitea_repo_path must be of the form '<owner>/<repo>'."
  }
}

variable "gitea_token_secret" {
  description = "Secret Manager secret id (just the short name, e.g. 'gitea-bootstrap-pat') holding a Gitea PAT with read access to the repo. The IAM module (#15) MUST grant the GPU SA roles/secretmanager.secretAccessor scoped to this single secret."
  type        = string
}

variable "claude_version" {
  description = "Pinned claude CLI version. The bootstrap script verifies the installed binary reports this exact version and refuses to start the agent if it does not match."
  type        = string
}

variable "git_user_email" {
  description = "Service identity for git user.email. Convention: tabula-enclave@<enclave>.internal."
  type        = string

  validation {
    condition     = can(regex("^[^@]+@[^@]+$", var.git_user_email))
    error_message = "git_user_email must look like an email address."
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
