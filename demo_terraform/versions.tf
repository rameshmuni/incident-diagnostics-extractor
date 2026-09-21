# -----------------------------------------------------------------------------
# versions.tf — pins Terraform itself and the Google provider.
#
# Real prod teams pin exact versions here on purpose: an unpinned provider can
# silently pick up a new major version between one `terraform apply` and the
# next, changing resource behavior underneath you with no code change to blame.
# -----------------------------------------------------------------------------

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
