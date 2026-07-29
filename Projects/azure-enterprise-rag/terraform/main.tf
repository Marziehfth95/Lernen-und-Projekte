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
      # Erlaubt Terraform, die alte, ungenutzte Gruppe mitsamt Inhalt zu löschen
      prevent_deletion_if_contains_resources = false
    }
  }
}

# 1. Ressourcengruppe (Der Container für alles)
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

# 3. Azure Container Registry (ACR)
resource "azurerm_container_registry" "acr" {
  name                = "acrragfattahi996" 
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = true
}

# 4. Container App Environment (Die Umgebung für den Container)
resource "azurerm_container_app_environment" "env" {
  name                = "cae-rag-app"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
}

# 5. Die Container App selbst
resource "azurerm_container_app" "rag_app" {
  name                         = "rag-app-2"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"

  # Geheimnisse (nur noch ACR und OpenAI nötig)
  secret {
    name  = "acr-password"
    value = azurerm_container_registry.acr.admin_password
  }
  secret {
    name  = "openai-key"
    value = azurerm_cognitive_account.openai.primary_access_key
  }

  # Registry-Zugang
  registry {
    server               = azurerm_container_registry.acr.login_server
    username             = azurerm_container_registry.acr.admin_username
    password_secret_name = "acr-password"
  }

  template {
    container {
      name   = "rag-app-container"
      image  = "acrragfattahi996.azurecr.io/rag-app:v20" # Version 20 für den sauberen Neustart
      cpu    = 0.5
      memory = "1.0Gi"

      # Umgebungsvariablen für Python
      env {
        name        = "OPENAI_API_KEY"
        secret_name = "openai-key"
      }
      env {
        name  = "OPENAI_ENDPOINT"
        value = azurerm_cognitive_account.openai.endpoint
      }
      
      # WICHTIG: Keine Volume Mounts oder Datenbank-Links mehr hier!
      # ChromaDB läuft jetzt glücklich auf dem lokalen Container-Speicher.
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