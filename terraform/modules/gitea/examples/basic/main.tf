###############################################################################
# Basic example invocation of the tabula enclave Gitea module.
#
# This example uses local state and is intended for `terraform validate` /
# `terraform plan` smoke testing. Real deployments should configure a remote
# backend in the caller and wire the network and IAM module outputs in.
###############################################################################

provider "google" {
  project = var.project_id
  region  = var.region
}

module "gitea" {
  source = "../../"

  project_id   = var.project_id
  region       = var.region
  zone         = var.zone
  enclave_name = var.enclave_name

  network_self_link = var.network_self_link
  subnet_self_link  = var.subnet_self_link
  gitea_sa_email    = var.gitea_sa_email

  # Defaults are sensible; override here if needed.
  # machine_type      = "e2-small"
  # gitea_version     = "1.22.3"
  # data_disk_size_gb = 50
  # boot_disk_size_gb = 20
}

output "instance_name" {
  value = module.gitea.instance_name
}

output "internal_fqdn" {
  value = module.gitea.internal_fqdn
}

output "internal_ip" {
  value = module.gitea.internal_ip
}

output "data_disk_id" {
  value = module.gitea.data_disk_id
}

output "zone" {
  value = module.gitea.zone
}
