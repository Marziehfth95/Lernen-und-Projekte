terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0"
    }
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}

# 1. Ressourcengruppe
resource "azurerm_resource_group" "rg" {
  name     = "rg-rag-app"
  location = "swedencentral"
}

# 2. Azure OpenAI Account
resource "azurerm_cognitive_account" "openai" {
  name                = "openai-rag-fattahi995"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  kind                = "OpenAI"
  sku_name            = "S0"
}

# Private network shared by Container Apps and PostgreSQL. The two services
# require separate delegated subnets.
resource "azurerm_virtual_network" "rag" {
  name                = "vnet-rag-app"
  address_space       = ["10.40.0.0/16"]
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
}

resource "azurerm_subnet" "container_apps" {
  name                 = "snet-container-apps"
  resource_group_name  = azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.rag.name
  address_prefixes     = ["10.40.0.0/23"]

  delegation {
    name = "container-apps-delegation"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "postgres" {
  name                 = "snet-postgres"
  resource_group_name  = azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.rag.name
  address_prefixes     = ["10.40.2.0/24"]

  delegation {
    name = "postgres-delegation"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_private_dns_zone" "postgres" {
  name                = "private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.rg.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "postgres-vnet-link"
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.rag.id
  resource_group_name   = azurerm_resource_group.rg.name
}

# 3. PostgreSQL Flexible Server
resource "azurerm_postgresql_flexible_server" "postgres" {
  name                          = "postgres-rag-fattahi995"
  resource_group_name           = azurerm_resource_group.rg.name
  location                      = azurerm_resource_group.rg.location
  version                       = "14"
  administrator_login           = "psqladmin"
  administrator_password        = var.db_password
  zone                          = "1"
  storage_mb                    = 32768
  sku_name                      = "B_Standard_B1ms"
  delegated_subnet_id           = azurerm_subnet.postgres.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

# 3.1 Pgvector Erweiterung in Azure freischalten
resource "azurerm_postgresql_flexible_server_configuration" "pgvector_extension" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "vector"
}

# 4. Azure Container Registry (ACR)
resource "azurerm_container_registry" "acr" {
  name                = "acrragfattahi996"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = false
}

resource "azurerm_user_assigned_identity" "rag_app" {
  name                = "id-rag-app"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.rag_app.principal_id
}

resource "azurerm_role_assignment" "openai_user" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.rag_app.principal_id
}

# 5. Container App Environment
resource "azurerm_container_app_environment" "env" {
  name                     = "cae-rag-app"
  location                 = azurerm_resource_group.rg.location
  resource_group_name      = azurerm_resource_group.rg.name
  infrastructure_subnet_id = azurerm_subnet.container_apps.id
}

# 6. Container App
resource "azurerm_container_app" "rag_app" {
  name                         = "rag-app-2"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.rag_app.id]
  }

  secret {
    name  = "db-password"
    value = var.db_password
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.rag_app.id
  }

  template {
    container {
      name   = "rag-app-container"
      image  = "acrragfattahi996.azurecr.io/rag-app:v21" # Neues Tag v21
      cpu    = 0.5
      memory = "1.0Gi"

      env {
        name  = "OPENAI_ENDPOINT"
        value = azurerm_cognitive_account.openai.endpoint
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.rag_app.client_id
      }
      env {
        name  = "AUTH_TENANT_ID"
        value = var.auth_tenant_id
      }
      env {
        name  = "AUTH_CLIENT_ID"
        value = var.auth_client_id
      }
      env {
        name  = "AUTH_REQUIRED_ROLE"
        value = var.auth_required_role
      }
      env {
        name  = "AUTH_REQUIRED_SCOPE"
        value = var.auth_required_scope
      }
      env {
        name  = "DB_HOST"
        value = azurerm_postgresql_flexible_server.postgres.fqdn
      }
      env {
        name  = "DB_USER"
        value = azurerm_postgresql_flexible_server.postgres.administrator_login
      }
      env {
        name        = "DB_PASS"
        secret_name = "db-password"
      }
      env {
        name  = "DB_NAME"
        value = "postgres"
      }
    }
  }

  ingress {
    external_enabled = var.external_ingress_enabled
    target_port      = 8000
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }


  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.openai_user
  ]
}

# 7. Azure OpenAI Deployment: Embedding Modell
resource "azurerm_cognitive_deployment" "embedding_model" {
  name                 = "text-embedding-3-small"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "text-embedding-3-small"
    version = "1"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
}

# 8. Azure OpenAI Deployment: Chat Modell (GPT-5.6 Luna)
resource "azurerm_cognitive_deployment" "chat_model" {
  name                 = "gpt-5.6-luna"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-5.6-luna"
    version = "2026-07-09"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
}
