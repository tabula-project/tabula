###############################################################################
# Example: classifier module wired against mocked VPC + IAM dependencies.
#
# Used as a `terraform validate` / `terraform plan` fixture. Real VPC and IAM
# modules don't exist in-repo yet (separate sub-issues of Epic #12), so this
# example takes their outputs as plain string variables and threads them in.
#
# Usage:
#   cd terraform/modules/classifier/examples/with-mocked-deps
#   terraform init -backend=false
#   terraform validate
#   terraform plan \
#     -var='project_id=tabula-enclave-dev' \
#     -var='subnet_id=projects/tabula-enclave-dev/regions/us-central1/subnetworks/enclave-private' \
#     -var='service_account_email=tabula-classifier@tabula-enclave-dev.iam.gserviceaccount.com'
###############################################################################

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "subnet_id" {
  type = string
}

variable "service_account_email" {
  type = string
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

module "classifier" {
  source = "../.."

  project_id = var.project_id
  region     = var.region
  zone       = var.zone

  subnet_id             = var.subnet_id
  service_account_email = var.service_account_email

  # Wake-signal target — empty until the GPU VM exists.
  gpu_instance_name = ""
  gpu_instance_zone = ""
}

output "instance_name" {
  value = module.classifier.instance_name
}

output "internal_ip" {
  value = module.classifier.internal_ip
}

output "self_link" {
  value = module.classifier.self_link
}
