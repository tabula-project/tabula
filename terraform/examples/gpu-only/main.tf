# Minimal stub for plan-testing the GPU module in isolation.
#
# This example does not depend on the network or IAM modules (issues #14 and
# #15) being implemented yet — it plumbs literal placeholder values through
# the module so `terraform validate` and `terraform plan` succeed without a
# full enclave wiring.
#
# Replace these literals with real module outputs when assembling the full
# enclave root module.

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  # Fake but well-formed self-links so the GPU module's variables typecheck.
  fake_network_self_link = "projects/${var.project_id}/global/networks/${var.enclave_name}-vpc"
  fake_subnet_self_link  = "projects/${var.project_id}/regions/${var.region}/subnetworks/${var.enclave_name}-subnet"
  fake_gpu_sa_email      = "${var.enclave_name}-gpu@${var.project_id}.iam.gserviceaccount.com"
}

module "gpu" {
  source = "../../modules/gpu"

  project_id   = var.project_id
  region       = var.region
  zone         = var.zone
  enclave_name = var.enclave_name

  network_self_link = local.fake_network_self_link
  subnet_self_link  = local.fake_subnet_self_link
  gpu_sa_email      = local.fake_gpu_sa_email
}

output "instance_name" {
  value = module.gpu.instance_name
}

output "instance_self_link" {
  value = module.gpu.instance_self_link
}

output "zone" {
  value = module.gpu.zone
}

output "stop_schedule_id" {
  value = module.gpu.stop_schedule_id
}

output "network_tags" {
  value = module.gpu.network_tags
}
