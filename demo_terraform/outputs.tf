# -----------------------------------------------------------------------------
# outputs.tf — the handful of values you'd actually want printed after an
# apply (or wired into a CI pipeline / demo script).
# -----------------------------------------------------------------------------

output "sdk_demo_app_url" {
  description = "Public URL of aiops-sdk-demo-app (end-user, suggest-only)."
  value       = google_cloud_run_v2_service.sdk_demo_app.uri
}

output "console_app_url" {
  description = "Public URL of aiops-console-app (ops reviewer, executes remedies)."
  value       = google_cloud_run_v2_service.console_app.uri
}

output "runtime_service_account_email" {
  description = "The single service account both Cloud Run services run as."
  value       = google_service_account.aiops_runtime.email
}

output "bq_dataset" {
  description = "BigQuery dataset holding incident_corpus + demo_state."
  value       = google_bigquery_dataset.aiops_corpus.dataset_id
}

output "artifact_registry_repo" {
  description = "Artifact Registry repo both Cloud Build triggers push images into."
  value       = google_artifact_registry_repository.aiops_repo.repository_id
}
