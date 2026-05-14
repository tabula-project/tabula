terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

# The google provider is configured by the CLI's terraform.tfvars (project,
# region) plus ADC (`gcloud auth application-default login`) at apply time.
# Plans against this composition REQUIRE valid credentials; this is the
# explicit trade-off vs. the stub composition (terraform/enclave/), which
# preserves offline-plan-ability.
provider "google" {
  project = var.project_id
  region  = var.region
}
