# -----------------------------------------------------------------------------
# main.tf — the AIOps demo's resources, illustrative for the Week 4 architecture
# defense. This is written against the ACTUAL 2-app demo architecture:
#
#   1. aiops-sdk-demo-app   (end-user facing, suggest-only, via AiopsSDK)
#   2. aiops-console-app    (ops reviewer facing, the only place remedies run)
#
# Both read/write the same BigQuery dataset, both run as the same service
# account, both deploy from the same Artifact Registry repo + Cloud Build
# trigger. ServiceNow is external to GCP and is only referenced here as
# secrets + env vars, never as a resource of its own.
# -----------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 1. Identity — one runtime service account shared by both Cloud Run services,
#    scoped with only the IAM roles they actually need at runtime.
# ---------------------------------------------------------------------------

resource "google_service_account" "aiops_runtime" {
  account_id   = var.service_account_id
  display_name = "AIOps demo runtime (Cloud Run: sdk-demo-app + console-app)"
}

# BigQuery: both apps read the corpus (VECTOR_SEARCH) and write demo_state.
resource "google_project_iam_member" "runtime_bq_data_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.aiops_runtime.email}"
}

# BigQuery: running VECTOR_SEARCH / query jobs requires jobUser at project level.
resource "google_project_iam_member" "runtime_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.aiops_runtime.email}"
}

# Secret Manager: both apps need to read SN_USER / SN_PASS / GEMINI_API_KEY at boot.
resource "google_project_iam_member" "runtime_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.aiops_runtime.email}"
}

# ---------------------------------------------------------------------------
# 2. Secrets — credentials that must never sit in a Cloud Run env var in
#    plain text or in this repo. Values come in via terraform.tfvars (which is
#    gitignored), not hardcoded.
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "sn_user" {
  secret_id = "aiops-servicenow-user"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "sn_user" {
  secret      = google_secret_manager_secret.sn_user.id
  secret_data = var.servicenow_user
}

resource "google_secret_manager_secret" "sn_pass" {
  secret_id = "aiops-servicenow-pass"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "sn_pass" {
  secret      = google_secret_manager_secret.sn_pass.id
  secret_data = var.servicenow_password
}

resource "google_secret_manager_secret" "gemini_key" {
  secret_id = "aiops-gemini-api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "gemini_key" {
  secret      = google_secret_manager_secret.gemini_key.id
  secret_data = var.gemini_api_key
}

# ---------------------------------------------------------------------------
# 3. Data layer — one dataset, two tables. Same corpus, same demo_state,
#    read and written by both Cloud Run services. No per-app database.
# ---------------------------------------------------------------------------

resource "google_bigquery_dataset" "aiops_corpus" {
  dataset_id  = var.bq_dataset_id
  location    = var.region
  description = "Incident corpus (embeddings for VECTOR_SEARCH) + demo/candidate state, shared by aiops-sdk-demo-app and aiops-console-app."
}

resource "google_bigquery_table" "incident_corpus" {
  dataset_id = google_bigquery_dataset.aiops_corpus.dataset_id
  table_id   = "incident_corpus"

  # Real schema is created by build_bigquery_corpus.py's SCHEMA constant;
  # Terraform only owns the table's existence + partitioning/lifecycle here,
  # not every column, so the ingestion script stays the source of truth
  # for the embedding schema.
  deletion_protection = false
}

resource "google_bigquery_table" "demo_state" {
  dataset_id = google_bigquery_dataset.aiops_corpus.dataset_id
  table_id   = "demo_state"

  deletion_protection = false
}

# ---------------------------------------------------------------------------
# 4. Build/deploy — one Artifact Registry repo for both images, one Cloud
#    Build trigger per app (same repo, different Dockerfile/context) so a
#    push to main rebuilds and redeploys automatically instead of relying on
#    a manual `gcloud run deploy --source .`.
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "aiops_repo" {
  location      = var.region
  repository_id = "aiops-repo"
  description   = "Container images for aiops-sdk-demo-app and aiops-console-app."
  format        = "DOCKER"
}

resource "google_cloudbuild_trigger" "demo_app_build" {
  name        = "aiops-sdk-demo-app-deploy"
  description = "Build + deploy aiops-sdk-demo-app on push to ${var.github_branch}."

  github {
    owner = var.github_owner
    name  = var.github_repo
    push {
      branch = var.github_branch
    }
  }

  included_files = ["aiops_sdk_demo_app/**", "aiops_sdk/**"]
  filename       = "aiops_sdk_demo_app/cloudbuild.yaml"
}

resource "google_cloudbuild_trigger" "console_app_build" {
  name        = "aiops-console-app-deploy"
  description = "Build + deploy aiops-console-app on push to ${var.github_branch}."

  github {
    owner = var.github_owner
    name  = var.github_repo
    push {
      branch = var.github_branch
    }
  }

  included_files = ["aiops_console_app/**", "aiops_sdk/**"]
  filename       = "aiops_console_app/cloudbuild.yaml"
}

# ---------------------------------------------------------------------------
# 5. Compute — the two Cloud Run services themselves. Same shape, same
#    service account, same secrets; only the image and the "audience"
#    differs (end-user suggest-only vs. ops reviewer who can execute fixes).
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "sdk_demo_app" {
  name     = "aiops-sdk-demo-app"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.aiops_runtime.email

    containers {
      image = var.demo_app_image

      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BQ_DATASET"
        value = google_bigquery_dataset.aiops_corpus.dataset_id
      }
      env {
        name  = "SERVICENOW_INSTANCE"
        value = var.servicenow_instance
      }
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SN_USER"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.sn_user.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SN_PASS"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.sn_pass.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.runtime_bq_data_editor,
    google_project_iam_member.runtime_bq_job_user,
    google_project_iam_member.runtime_secret_accessor,
  ]
}

resource "google_cloud_run_v2_service" "console_app" {
  name     = "aiops-console-app"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.aiops_runtime.email

    containers {
      image = var.console_app_image

      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BQ_DATASET"
        value = google_bigquery_dataset.aiops_corpus.dataset_id
      }
      env {
        name  = "SERVICENOW_INSTANCE"
        value = var.servicenow_instance
      }
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SN_USER"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.sn_user.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SN_PASS"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.sn_pass.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.runtime_bq_data_editor,
    google_project_iam_member.runtime_bq_job_user,
    google_project_iam_member.runtime_secret_accessor,
  ]
}

# ---------------------------------------------------------------------------
# 6. Public access — matches how the demo is actually deployed today
#    (`gcloud run deploy --allow-unauthenticated`). In a real prod rollout
#    this block is exactly what you'd delete/tighten first — swap for an
#    IAM-bound invoker list or put both services behind Identity-Aware Proxy.
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service_iam_member" "sdk_demo_app_public" {
  count    = var.allow_unauthenticated ? 1 : 0
  location = google_cloud_run_v2_service.sdk_demo_app.location
  name     = google_cloud_run_v2_service.sdk_demo_app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "console_app_public" {
  count    = var.allow_unauthenticated ? 1 : 0
  location = google_cloud_run_v2_service.console_app.location
  name     = google_cloud_run_v2_service.console_app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
