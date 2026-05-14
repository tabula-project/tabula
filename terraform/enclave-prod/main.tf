# terraform/enclave-prod/main.tf
#
# Production composition: wires the real sibling modules under
# `terraform/modules/<name>/`. Unlike `terraform/enclave/` (the stub
# composition), this composition makes real GCP API calls at plan time and
# REQUIRES Application Default Credentials. See README.md for the design
# rationale; see `terraform/enclave/` if you need offline-plan-ability.

###############################################################################
# Network — VPC + private subnet + Cloud NAT + base egress firewall rules
###############################################################################

module "network" {
  source = "../modules/network"

  project_id   = var.project_id
  region       = var.region
  enclave_name = var.enclave_name
}

###############################################################################
# IAM — three workload service accounts + the gpu_waker custom role
#
# `gpu_instance_id` is intentionally left at its default (null); the
# classifier->GPU wake binding is created by the GPU module after the
# instance exists, avoiding the IAM/GPU chicken-and-egg.
###############################################################################

module "iam" {
  source = "../modules/iam"

  project_id   = var.project_id
  enclave_name = var.enclave_name
}

###############################################################################
# Firewall — deny-all baseline + targeted allows. Sits on top of the network
# module's egress rules.
###############################################################################

module "firewall" {
  source = "../modules/firewall"

  project_id  = var.project_id
  network     = module.network.vpc_self_link
  name_prefix = var.enclave_name
  noise_port  = var.noise_port
}

###############################################################################
# GPU — T4 host + sleep schedule + bootstrap. The wake-signal IAM binding
# from the classifier SA is created inside this module once the instance
# exists, closing the IAM/GPU dependency cycle.
###############################################################################

module "gpu" {
  source = "../modules/gpu"

  project_id        = var.project_id
  region            = var.region
  zone              = var.zone
  enclave_name      = var.enclave_name
  network_self_link = module.network.vpc_self_link
  subnet_self_link  = module.network.subnet_self_link
  gpu_sa_email      = module.iam.gpu_sa_email
}

###############################################################################
# Classifier — always-on small VM that holds the Noise XX endpoint, classifies
# inbound, and wakes the GPU on demand.
###############################################################################

module "classifier" {
  source = "../modules/classifier"

  project_id            = var.project_id
  region                = var.region
  zone                  = var.zone
  subnet_id             = module.network.subnet_id
  service_account_email = module.iam.classifier_sa_email
  gpu_instance_name     = module.gpu.instance_name
  gpu_instance_zone     = module.gpu.zone
}

###############################################################################
# Gitea — internal git server for L1 substrate mirroring.
###############################################################################

module "gitea" {
  source = "../modules/gitea"

  project_id        = var.project_id
  region            = var.region
  zone              = var.zone
  enclave_name      = var.enclave_name
  network_self_link = module.network.vpc_self_link
  subnet_self_link  = module.network.subnet_self_link
  gitea_sa_email    = module.iam.gitea_sa_email
}

###############################################################################
# Vertex PSC — private connectivity to Vertex AI (no public Vertex egress).
###############################################################################

module "vertex_psc" {
  source = "../modules/vertex-psc"

  project_id   = var.project_id
  region       = var.region
  enclave_name = var.enclave_name
  vpc_id       = module.network.vpc_id
  subnet_id    = module.network.subnet_id
}
