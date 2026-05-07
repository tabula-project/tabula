###############################################################################
# Basic example invocation of the tabula enclave network module.
#
# This example uses local state and is intended for `terraform validate` /
# `terraform plan` smoke testing. Real deployments should configure a remote
# backend in the caller.
###############################################################################

provider "google" {
  project = var.project_id
  region  = var.region
}

module "network" {
  source = "../../"

  project_id   = var.project_id
  region       = var.region
  enclave_name = var.enclave_name

  # Defaults are sensible; override here if needed.
  # subnet_cidr        = "10.10.0.0/24"
  # nat_log_filter     = "ERRORS_ONLY"
  # flow_log_sampling  = 0.5
  # extra_egress_cidrs = []
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "subnet_id" {
  value = module.network.subnet_id
}

output "workload_network_tag" {
  value = module.network.workload_network_tag
}
