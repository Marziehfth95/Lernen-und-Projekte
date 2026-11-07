locals {
  suffix = "${var.name_prefix}-${var.environment}"
  # Non-production demo only: the workflow app keeps its records in a
  # container-local SQLite file that every restart, revision or scale-in erases.
  use_postgresql            = var.workflow_persistence == "postgresql"
  ephemeral_sqlite_database = "sqlite:////tmp/workflow-demo/workflow.sqlite3"
  # The server can exist before the workflow app switches to it (staged rollout), and
  # stays when the app falls back to the demo database. Switching implies provisioning.
  provision_postgresql = var.provision_postgresql || local.use_postgresql
  tags = {
    application = "it-service-process-optimizer"
    environment = var.environment
    managed-by  = "terraform"
    data-class  = "aggregate-only"
  }
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.suffix}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  # Demo cost cap: ingestion stops for the rest of the UTC day once reached.
  daily_quota_gb = var.log_analytics_daily_quota_gb
  tags           = local.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.tags
}

resource "azurerm_container_registry" "main" {
  name                = "${var.name_prefix}${var.environment}acr"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "workflow" {
  name                = "id-workflow-${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "database_admin" {
  name                = "id-dbadmin-${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "migration" {
  name                = "id-migration-${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

# The workflow tier (VNet, PostgreSQL, Container Apps environment) runs in
# `workflow_location`. PostgreSQL private access needs its VNet in the server's
# region. The Hosted Agent reaches the workflow API through its public,
# Entra-protected ingress, so it does not need to share a region with it.
resource "azurerm_virtual_network" "main" {
  name                = "vnet-${local.suffix}"
  address_space       = ["10.42.0.0/16"]
  location            = var.workflow_location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_subnet" "container_apps" {
  name                 = "snet-container-apps"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.0.0/23"]

  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }

  # Subnets carry no location. Without this, a VNet move would delete them in
  # Azure while Terraform still tracks them.
  lifecycle {
    replace_triggered_by = [azurerm_virtual_network.main.location]
  }
}

resource "azurerm_subnet" "postgresql" {
  name                 = "snet-postgresql"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.4.0/24"]
  # Azure adds this endpoint (WAL traffic to storage) when it first provisions a server
  # in the subnet, and Microsoft warns against removing it. Declared so plans never strip it.
  service_endpoints = ["Microsoft.Storage"]

  delegation {
    name = "postgresql-flexible-server"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }

  lifecycle {
    replace_triggered_by = [azurerm_virtual_network.main.location]
  }
}

resource "azurerm_private_dns_zone" "postgresql" {
  name                = "private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgresql" {
  name                  = "link-${local.suffix}-postgresql"
  private_dns_zone_name = azurerm_private_dns_zone.postgresql.name
  virtual_network_id    = azurerm_virtual_network.main.id
  resource_group_name   = azurerm_resource_group.main.name
  tags                  = local.tags

  # The VNet ID is unchanged by a move, so relink explicitly.
  lifecycle {
    replace_triggered_by = [azurerm_virtual_network.main.location]
  }
}

resource "azurerm_role_assignment" "workflow_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.workflow.principal_id
}

# Consumption-only environment (no workload profiles, no private endpoint), so
# it carries no hourly environment-management charge. It logs to the workspace
# in `location` and pulls images from the ACR there through public endpoints.
resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.suffix}"
  location                   = var.workflow_location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id   = azurerm_subnet.container_apps.id
  tags                       = local.tags

  # Azure created the environment with this implicit profile; declaring it keeps
  # Terraform from trying to remove it. No dedicated (billed) profiles.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

# zone: null lets Azure choose; a pinned zone only places the single server (no HA block).
resource "azurerm_postgresql_flexible_server" "workflow" {
  count                         = local.provision_postgresql ? 1 : 0
  name                          = "psql-${local.suffix}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = var.workflow_location
  version                       = "16"
  sku_name                      = "B_Standard_B1ms"
  storage_mb                    = 32768
  auto_grow_enabled             = false
  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  zone                          = var.postgresql_zone
  delegated_subnet_id           = azurerm_subnet.postgresql.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgresql.id
  public_network_access_enabled = false

  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = false
    tenant_id                     = var.entra_tenant_id
  }

  tags = local.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgresql]
}

resource "azurerm_postgresql_flexible_server_database" "workflow" {
  count     = local.provision_postgresql ? 1 : 0
  name      = "process_optimizer"
  server_id = azurerm_postgresql_flexible_server.workflow[0].id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_active_directory_administrator" "migration" {
  count               = local.provision_postgresql ? 1 : 0
  server_name         = azurerm_postgresql_flexible_server.workflow[0].name
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = var.entra_tenant_id
  object_id           = azurerm_user_assigned_identity.database_admin.principal_id
  principal_name      = azurerm_user_assigned_identity.database_admin.name
  principal_type      = "ServicePrincipal"
}

resource "azurerm_container_app" "workflow" {
  count                        = var.deploy_workflow ? 1 : 0
  name                         = "ca-workflow-${local.suffix}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  # Azure placed the app on the environment's implicit Consumption profile;
  # declaring it keeps Terraform from trying to unset it.
  workload_profile_name = "Consumption"
  tags                  = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workflow.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workflow.id
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    # Scale-to-zero would erase the ephemeral SQLite file, so the demo keeps
    # exactly one replica. max_replicas stays 1: SQLite is not shared storage.
    min_replicas = local.use_postgresql ? 0 : 1
    max_replicas = 1
    container {
      name   = "workflow"
      image  = var.workflow_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "ENTRA_TENANT_ID"
        value = var.entra_tenant_id
      }
      # v2.0 access tokens always carry the API client ID as `aud`, and the
      # application validates the v2.0 issuer.
      env {
        name  = "ENTRA_AUDIENCE"
        value = var.entra_api_client_id
      }
      env {
        name  = "ENTRA_ISSUER"
        value = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0"
      }
      env {
        name  = "ENTRA_JWKS_URL"
        value = var.entra_jwks_url
      }
      env {
        name  = "ENTRA_ALLOWED_SERVICE_CLIENT_IDS"
        value = join(",", var.aggregate_service_client_ids)
      }
      env {
        name  = "CONTAINER_APPS_AUTH_ENABLED"
        value = "true"
      }
      env {
        name  = "WORKFLOW_PERSISTENCE_MODE"
        value = var.workflow_persistence
      }
      env {
        name  = "WORKFLOW_DATABASE_AUTH_MODE"
        value = local.use_postgresql ? "entra" : "none"
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.workflow.client_id
      }
      env {
        name = "WORKFLOW_DATABASE_URL"
        value = (
          local.use_postgresql
          ? "postgresql+psycopg://${azurerm_user_assigned_identity.workflow.name}@${one(azurerm_postgresql_flexible_server.workflow[*].fqdn)}/process_optimizer?sslmode=require&options=-csearch_path%3Dprocess_optimizer"
          : local.ephemeral_sqlite_database
        )
      }
      env {
        name  = "PROCESS_OPTIMIZER_ANALYSIS_DIR"
        value = var.analysis_directory
      }
      env {
        name  = "AGGREGATE_MANIFEST_PATH"
        value = var.aggregate_manifest_path
      }
      env {
        name  = "ENABLE_AZURE_MONITOR"
        value = "true"
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = azurerm_application_insights.main.connection_string
      }
      # SDK self-telemetry (Statsbeat) to a Microsoft-owned resource; app telemetry is unaffected.
      env {
        name  = "APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL"
        value = "true"
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/health"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/readiness"
      }
    }
  }

  lifecycle {
    precondition {
      condition     = can(regex("@sha256:[0-9a-f]{64}$", var.workflow_image))
      error_message = "deploy_workflow requires an immutable image reference by SHA-256 digest."
    }
    precondition {
      condition     = local.use_postgresql || var.environment != "prod"
      error_message = "Ephemeral SQLite workflow persistence is a non-production demo mode."
    }
  }
}

resource "azapi_resource" "workflow_auth" {
  count     = var.deploy_workflow ? 1 : 0
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  name      = "current"
  parent_id = azurerm_container_app.workflow[0].id

  body = {
    properties = {
      platform = {
        enabled        = true
        runtimeVersion = "~1"
      }
      # Only the data-free probe endpoints bypass Easy Auth, so an intercepted
      # probe can never restart the replica and erase the demo SQLite file.
      # Every business route still requires a token the app validates itself.
      globalValidation = {
        unauthenticatedClientAction = "Return401"
        excludedPaths               = ["/health", "/readiness"]
      }
      httpSettings = {
        requireHttps = true
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId     = var.entra_api_client_id
            openIdIssuer = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0"
          }
          validation = {
            allowedAudiences = [var.entra_api_client_id]
          }
        }
      }
      # Bearer-token API only: no browser sessions, so no token store.
      login = {
        tokenStore = {
          enabled = false
        }
      }
    }
  }
}
