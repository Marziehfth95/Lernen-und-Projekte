variable "environment" {
  type        = string
  description = "Short environment name used in resource names."
  validation {
    condition     = contains(["dev", "test", "prod"], var.environment)
    error_message = "environment must be dev, test, or prod."
  }
}

variable "location" {
  type        = string
  description = "Azure region of the resource group, Foundry, ACR, monitoring and managed identities, selected after residency and service availability review."
}

variable "workflow_location" {
  type        = string
  description = "Azure region of the workflow tier only: VNet, subnets, PostgreSQL and the Container Apps environment. May differ from `location` when PostgreSQL or Container Apps cannot be provisioned there."
  validation {
    condition     = can(regex("^[a-z0-9]+$", var.workflow_location))
    error_message = "workflow_location must be a lowercase Azure region name such as swedencentral."
  }
}

variable "name_prefix" {
  type        = string
  description = "Globally unique lowercase prefix."
  validation {
    condition     = can(regex("^[a-z0-9]{3,12}$", var.name_prefix))
    error_message = "name_prefix must be 3-12 lowercase alphanumeric characters."
  }
}

variable "workflow_image" {
  type        = string
  description = "Immutable ACR workflow image reference by digest; empty during foundation bootstrap."
  default     = ""
  validation {
    condition = (
      var.workflow_image == "" ||
      can(regex("^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$", var.workflow_image))
    )
    error_message = "workflow_image must be empty or an immutable registry reference by SHA-256 digest."
  }
}

variable "deploy_workflow" {
  type        = bool
  description = "Create the workflow Container App only after its immutable image exists."
  default     = false
}

variable "workflow_persistence" {
  type        = string
  description = "Workflow API persistence. `postgresql` is the durable design. `ephemeral_sqlite_demo` skips PostgreSQL and runs one replica on a container-local SQLite file that is lost on restart: non-production demo only."
  default     = "postgresql"
  validation {
    condition     = contains(["postgresql", "ephemeral_sqlite_demo"], var.workflow_persistence)
    error_message = "workflow_persistence must be postgresql or ephemeral_sqlite_demo."
  }
}

variable "entra_tenant_id" {
  type        = string
  description = "Allowed Microsoft Entra tenant ID."
}

variable "entra_api_audience" {
  type        = string
  description = "Workflow API application ID URI; callers request the `<uri>/.default` scope."
}

variable "entra_api_client_id" {
  type        = string
  description = "Workflow API client ID. The app registration must issue v2.0 access tokens (requestedAccessTokenVersion = 2), whose `aud` is this ID."
}

variable "entra_jwks_url" {
  type        = string
  description = "Documented Entra v2 signing-key endpoint."
}

variable "aggregate_service_client_ids" {
  type        = list(string)
  description = "Hosted Agent identity client IDs (token `azp`) allowed to call aggregate endpoints. Empty admits no service identity."
  default     = []
}

variable "model_name" {
  type        = string
  description = "Foundry model name for the Global Standard deployment."
  default     = "gpt-4.1-mini"
}

variable "model_version" {
  type        = string
  description = "Pinned model version. Confirm it is deployable for this subscription before apply."
  default     = "2025-04-14"
}

variable "model_deployment_name" {
  type        = string
  description = "Deployment name passed to the Hosted Agent as MODEL_DEPLOYMENT_NAME."
  default     = "gpt-4.1-mini"
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$", var.model_deployment_name))
    error_message = "model_deployment_name must be 2-64 characters of letters, digits, '.', '_' or '-'."
  }
}

variable "model_sku_name" {
  type        = string
  description = "Standard pay-per-token deployment type with quota in the region (gpt-4.1-mini: GlobalStandard; gpt-5.4-mini: DataZoneStandard)."
  default     = "GlobalStandard"
  validation {
    condition     = contains(["GlobalStandard", "DataZoneStandard"], var.model_sku_name)
    error_message = "model_sku_name must be GlobalStandard or DataZoneStandard; provisioned and batch SKUs are out of scope."
  }
}

variable "model_capacity_thousand_tpm" {
  type        = number
  description = "Global Standard rate limit in thousands of tokens per minute. Not a spending cap."
  default     = 20
  validation {
    condition     = var.model_capacity_thousand_tpm >= 1 && var.model_capacity_thousand_tpm <= 50 && floor(var.model_capacity_thousand_tpm) == var.model_capacity_thousand_tpm
    error_message = "model_capacity_thousand_tpm must be an integer from 1 to 50 for the demo."
  }
}

variable "agent_deployer_principal_id" {
  type        = string
  description = "Object ID of the user or CI principal that runs `azd deploy`. Null skips its role assignments."
  default     = null
  nullable    = true
  validation {
    condition = (
      var.agent_deployer_principal_id == null ||
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.agent_deployer_principal_id))
    )
    error_message = "agent_deployer_principal_id must be null or a UUID object ID."
  }
}

variable "agent_deployer_project_role_name" {
  type        = string
  description = "Built-in Foundry role granting agent-version deployment on the project. Verify the current name with `az role definition list --name`."
  default     = "Foundry Project Manager"
}

variable "log_analytics_daily_quota_gb" {
  type        = number
  description = "Daily Log Analytics ingestion cap in GB. Telemetry is dropped after the cap for the rest of the day."
  default     = 0.2
  validation {
    condition     = var.log_analytics_daily_quota_gb >= 0.023 && var.log_analytics_daily_quota_gb <= 1
    error_message = "log_analytics_daily_quota_gb must be between 0.023 and 1 for the demo."
  }
}

variable "budget_amount" {
  type        = number
  description = "Monthly pre-tax resource-group budget in billing currency (EUR). 25 is about 30 EUR including 19% VAT."
  default     = 25
  validation {
    condition     = var.budget_amount > 0 && var.budget_amount <= 25
    error_message = "budget_amount must be greater than 0 and at most 25 (30 EUR gross at 19% VAT)."
  }
}

variable "budget_start_date" {
  type        = string
  description = "Budget start date: the first day of the current month, for example 2026-10-01T00:00:00Z."
  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-01T00:00:00Z$", var.budget_start_date))
    error_message = "budget_start_date must be the first day of a month in RFC 3339 UTC format."
  }
}

variable "budget_contact_emails" {
  type        = list(string)
  description = "Recipients of budget threshold notifications."
  validation {
    condition     = length(var.budget_contact_emails) > 0
    error_message = "At least one budget contact email is required."
  }
}

variable "aggregate_manifest_path" {
  type        = string
  description = "Image path of the hash-bound aggregate manifest in the workflow container."
  default     = "/mnt/aggregates/aggregate_manifest.json"
}

variable "analysis_directory" {
  type        = string
  description = "Mounted directory containing only approved aggregate artifacts."
  default     = "/mnt/aggregates"
}

variable "provision_postgresql" {
  type        = bool
  description = "Create the PostgreSQL server, database and Entra admin without switching the workflow app (staged rollout), and keep them if the app falls back to ephemeral_sqlite_demo. workflow_persistence = postgresql always provisions them."
  default     = false
}

variable "postgresql_zone" {
  type        = string
  description = "Availability zone for the single PostgreSQL server (\"1\", \"2\" or \"3\"), or null to let Azure choose. High availability stays disabled."
  default     = null
  validation {
    condition     = var.postgresql_zone == null || contains(["1", "2", "3"], coalesce(var.postgresql_zone, "none"))
    error_message = "postgresql_zone must be null, \"1\", \"2\" or \"3\"."
  }
}

variable "deploy_database_jobs" {
  type        = bool
  description = "Create the manually triggered PostgreSQL bootstrap and migration Container Apps jobs. Requires a provisioned server (provision_postgresql or workflow_persistence = postgresql). Jobs cost nothing while idle and never run on deploy."
  default     = false
}
