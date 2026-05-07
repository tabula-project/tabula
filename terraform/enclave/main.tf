# terraform/enclave/main.tf
#
# Root module composing the sibling modules into one enclave.
#
# Each `source = "./stubs/<name>"` reference is a placeholder; swap it for
# the real module's path/source as the corresponding sibling-module issue
# merges (see README.md for the issue-to-stub mapping).

module "network" {
  source = "./stubs/network"

  project_id   = var.project_id
  region       = var.region
  enclave_name = var.enclave_name
}

module "iam" {
  source = "./stubs/iam"

  project_id   = var.project_id
  enclave_name = var.enclave_name
}

module "firewall" {
  source = "./stubs/firewall"

  project_id   = var.project_id
  enclave_name = var.enclave_name
  network_id   = module.network.network_id
}

module "classifier" {
  source = "./stubs/classifier"

  project_id          = var.project_id
  region              = var.region
  enclave_name        = var.enclave_name
  network_id          = module.network.network_id
  service_account     = module.iam.classifier_sa_email
}

module "gpu" {
  source = "./stubs/gpu"

  project_id      = var.project_id
  region          = var.region
  enclave_name    = var.enclave_name
  network_id      = module.network.network_id
  service_account = module.iam.gpu_sa_email
}

module "gitea" {
  source = "./stubs/gitea"

  project_id      = var.project_id
  region          = var.region
  enclave_name    = var.enclave_name
  network_id      = module.network.network_id
  service_account = module.iam.gitea_sa_email
}

module "vertex_psc" {
  source = "./stubs/vertex_psc"

  project_id   = var.project_id
  region       = var.region
  enclave_name = var.enclave_name
  network_id   = module.network.network_id
}
