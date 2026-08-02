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

# 3. PostgreSQL Flexible Server
resource "azurerm_postgresql_flexible_server" "postgres" {
  name                   = "postgres-rag-fattahi995"
  resource_group_name    = azurerm_resource_group.rg.name
  location               = azurerm_resource_group.rg.location
  version                = "14"
  administrator_login    = "psqladmin"
  administrator_password = var.db_password
  zone                   = "1"
  storage_mb             = 32768
  sku_name               = "B_Standard_B1ms"
}

# 3.1 Pgvector-Erweiterung in Azure freischalten
resource "azurerm_postgresql_flexible_server_configuration" "pgvector_extension" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "vector"
}

# 3.2 Firewall-Regel (Zugriff für Azure-Dienste erlauben)
resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.postgres.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# 4. Azure Container Registry (ACR)
resource "azurerm_container_registry" "acr" {
  name                = "acrragfattahi996"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = true
}

# 5. Container App Environment
resource "azurerm_container_app_environment" "env" {
  name                = "cae-rag-app"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
}

# 6. Container App
resource "azurerm_container_app" "rag_app" {
  name                         = "rag-app-2"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.acr.admin_password
  }
  secret {
    name  = "openai-key"
    value = azurerm_cognitive_account.openai.primary_access_key
  }
  secret {
    name  = "db-password"
    value = var.db_password
  }

  registry {
    server               = azurerm_container_registry.acr.login_server
    username             = azurerm_container_registry.acr.admin_username
    password_secret_name = "acr-password"
  }

  template {
    container {
      name   = "rag-app-container"
      image  = "acrragfattahi996.azurecr.io/rag-app:v21" # Neues Tag v21
      cpu    = 0.5
      memory = "1.0Gi"

      env {
        name        = "OPENAI_API_KEY"
        secret_name = "openai-key"
      }
      env {
        name  = "OPENAI_ENDPOINT"
        value = azurerm_cognitive_account.openai.endpoint
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
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
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