# Manually triggered, one-shot jobs for the durable PostgreSQL workflow database.
# They are the only components that change the database or its schema; the workflow
# API verifies the migration level at startup and never runs DDL.
#
# Order (each start needs separate approval):
#   1. az containerapp job start -n caj-dbboot-<suffix> -g <rg>   (Entra admin: roles, PUBLIC revokes)
#   2. az containerapp job start -n caj-migrate-<suffix> -g <rg>  (migration identity: schema, migrations, runtime grants)
# Both use the reviewed workflow image (same digest as the API) and pull it with the
# workflow identity, which already holds AcrPull; no new role assignment is needed.

locals {
  deploy_database_jobs = var.deploy_database_jobs && local.provision_postgresql
  postgres_fqdn        = one(azurerm_postgresql_flexible_server.workflow[*].fqdn)
}

resource "azurerm_container_app_job" "database_bootstrap" {
  count                        = local.deploy_database_jobs ? 1 : 0
  name                         = "caj-dbboot-${local.suffix}"
  location                     = var.workflow_location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 300
  replica_retry_limit          = 0
  tags                         = local.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type = "UserAssigned"
    identity_ids = [
      azurerm_user_assigned_identity.database_admin.id,
      azurerm_user_assigned_identity.workflow.id,
    ]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workflow.id
  }

  template {
    container {
      name    = "bootstrap"
      image   = var.workflow_image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["python", "-m", "process_optimizer.persistence.bootstrap"]

      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "WORKFLOW_PERSISTENCE_MODE"
        value = "postgresql"
      }
      env {
        name  = "WORKFLOW_DATABASE_AUTH_MODE"
        value = "entra"
      }
      # Token is requested for the Entra administrator identity only.
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.database_admin.client_id
      }
      env {
        name  = "WORKFLOW_DATABASE_URL"
        value = "postgresql+psycopg://${azurerm_user_assigned_identity.database_admin.name}@${local.postgres_fqdn}/process_optimizer?sslmode=require"
      }
      env {
        name  = "RUNTIME_PRINCIPAL_NAME"
        value = azurerm_user_assigned_identity.workflow.name
      }
      env {
        name  = "RUNTIME_PRINCIPAL_OBJECT_ID"
        value = azurerm_user_assigned_identity.workflow.principal_id
      }
      env {
        name  = "MIGRATION_PRINCIPAL_NAME"
        value = azurerm_user_assigned_identity.migration.name
      }
      env {
        name  = "MIGRATION_PRINCIPAL_OBJECT_ID"
        value = azurerm_user_assigned_identity.migration.principal_id
      }
    }
  }

  lifecycle {
    precondition {
      condition     = can(regex("@sha256:[0-9a-f]{64}$", var.workflow_image))
      error_message = "Database jobs require an immutable workflow image reference by digest."
    }
  }

  depends_on = [azurerm_postgresql_flexible_server_active_directory_administrator.migration]
}

resource "azurerm_container_app_job" "database_migration" {
  count                        = local.deploy_database_jobs ? 1 : 0
  name                         = "caj-migrate-${local.suffix}"
  location                     = var.workflow_location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 600
  replica_retry_limit          = 0
  tags                         = local.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type = "UserAssigned"
    identity_ids = [
      azurerm_user_assigned_identity.migration.id,
      azurerm_user_assigned_identity.workflow.id,
    ]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workflow.id
  }

  template {
    container {
      name    = "migrate"
      image   = var.workflow_image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["python", "-m", "process_optimizer.persistence.cli", "--apply"]

      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "WORKFLOW_PERSISTENCE_MODE"
        value = "postgresql"
      }
      env {
        name  = "WORKFLOW_DATABASE_AUTH_MODE"
        value = "entra"
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.migration.client_id
      }
      env {
        name  = "WORKFLOW_DATABASE_URL"
        value = "postgresql+psycopg://${azurerm_user_assigned_identity.migration.name}@${local.postgres_fqdn}/process_optimizer?sslmode=require&options=-csearch_path%3Dprocess_optimizer"
      }
      # Job-only switch; an operator shell without it cannot migrate the durable database.
      env {
        name  = "WORKFLOW_MIGRATION_APPLY"
        value = "true"
      }
      env {
        name  = "WORKFLOW_DATABASE_SCHEMA"
        value = "process_optimizer"
      }
      env {
        name  = "WORKFLOW_RUNTIME_ROLE"
        value = azurerm_user_assigned_identity.workflow.name
      }
    }
  }

  lifecycle {
    precondition {
      condition     = can(regex("@sha256:[0-9a-f]{64}$", var.workflow_image))
      error_message = "Database jobs require an immutable workflow image reference by digest."
    }
  }

  depends_on = [azurerm_container_app_job.database_bootstrap]
}
