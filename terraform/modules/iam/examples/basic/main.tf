terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
}

module "iam" {
  source = "../.."

  project_id   = var.project_id
  enclave_name = var.enclave_name

  # Leave gpu_instance_id null on the first apply; the GPU VM module
  # will add the wake binding after creating the instance.
  gpu_instance_id        = null
  create_cli_operator_sa = false
}

output "classifier_sa_email" {
  value = module.iam.classifier_sa_email
}

output "gpu_sa_email" {
  value = module.iam.gpu_sa_email
}

output "gitea_sa_email" {
  value = module.iam.gitea_sa_email
}

output "gpu_waker_role_id" {
  value = module.iam.gpu_waker_role_id
}
