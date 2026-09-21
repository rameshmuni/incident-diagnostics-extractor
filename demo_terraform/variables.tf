# -----------------------------------------------------------------------------
# variables.tf — every knob this configuration needs, with no secrets hardcoded.
#
# Anything marked sensitive = true is a value Terraform will still store in its
# state file (that's a separate problem, solved by remote state + encryption),
# but it will at least never echo it back to your terminal or CI logs.
# -----------------------------------------------------------------------------

variable "project_id" {
  description = "GCP project ID that hosts the AIOps demo (e.g. incident-assistant-507518)."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry, and BigQuery dataset location."
  type        = string
  default     = "us-central1"
}

variable "service_account_id" {
  description = "Account ID (short name) for the single runtime service account shared by both Cloud Run apps."
  type        = string
  default     = "aiops-runtime"
}

variable "demo_app_image" {
  description = "Fully-qualified Artifact Registry image URI for aiops-sdk-demo-app, e.g. us-central1-docker.pkg.dev/PROJECT/aiops-repo/aiops-sdk-demo-app:latest"
  type        = string
}

variable "console_app_image" {
  description = "Fully-qualified Artifact Registry image URI for aiops-console-app."
  type        = string
}

variable "bq_dataset_id" {
  description = "BigQuery dataset name holding the incident corpus and demo state tables."
  type        = string
  default     = "incident_assistant"
}

variable "github_owner" {
  description = "GitHub org/user that owns the source repo, used to wire the Cloud Build trigger."
  type        = string
}

variable "github_repo" {
  description = "GitHub repo name for the Cloud Build trigger (push-to-branch build)."
  type        = string
  default     = "incident-diagnostics-extractor"
}

variable "github_branch" {
  description = "Branch pattern that fires a build, e.g. ^main$."
  type        = string
  default     = "^main$"
}

variable "servicenow_instance" {
  description = "ServiceNow instance hostname, stored as a plain env var (not a secret) since it's not sensitive by itself."
  type        = string
  default     = ""
}

variable "servicenow_user" {
  description = "ServiceNow API username. Stored in Secret Manager, not passed as a plain Cloud Run env var."
  type        = string
  sensitive   = true
  default     = ""
}

variable "servicenow_password" {
  description = "ServiceNow API password. Stored in Secret Manager."
  type        = string
  sensitive   = true
  default     = ""
}

variable "gemini_api_key" {
  description = "Gemini API key used for embeddings + generation. Stored in Secret Manager."
  type        = string
  sensitive   = true
  default     = ""
}

variable "allow_unauthenticated" {
  description = "Whether both Cloud Run services get public (unauthenticated) invoker access, matching how the demo is actually deployed today."
  type        = bool
  default     = true
}
