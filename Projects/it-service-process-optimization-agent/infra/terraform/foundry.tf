# Terraform is the sole owner of the Foundry account, project and model
# deployment. azd only adopts the project endpoint (azure.yaml `endpoint`) and
# creates Hosted Agent versions inside it; it must never provision these.

locals {
  foundry_account_name = "aif${var.name_prefix}${var.environment}"
  foundry_project_name = "proj-${local.suffix}"
}

resource "azapi_resource" "foundry_account" {
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = local.foundry_account_name
  parent_id = azurerm_resource_group.main.id
  location  = var.location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  body = {
    kind = "AIServices"
    sku = {
      name = "S0"
    }
    properties = {
      allowProjectManagement = true
      customSubDomainName    = local.foundry_account_name
      # Entra-only data plane: no account API keys can be used.
      disableLocalAuth = true
      # Public development endpoint protected by Entra. Network injection is a
      # creation-time decision and is intentionally not used for this demo.
      publicNetworkAccess = "Enabled"
    }
  }
}

resource "azapi_resource" "foundry_project" {
  type      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name      = local.foundry_project_name
  parent_id = azapi_resource.foundry_account.id
  location  = var.location
  tags      = local.tags

  # The project identity type cannot be changed after creation.
  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      displayName = "IT service process optimizer (${var.environment})"
      description = "Aggregate-only Hosted Agent project. Managed by Terraform."
    }
  }
}

resource "azapi_resource" "model_deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = var.model_deployment_name
  parent_id = azapi_resource.foundry_account.id

  body = {
    sku = {
      name = var.model_sku_name
      # Capacity is a rate limit in thousands of tokens per minute, not a
      # spending cap.
      capacity = var.model_capacity_thousand_tpm
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.model_name
        version = var.model_version
      }
      # Never silently switch the evaluated model version.
      versionUpgradeOption = "NoAutoUpgrade"
    }
  }

  # Serialize account child operations to avoid concurrent-update conflicts.
  depends_on = [azapi_resource.foundry_project]
}

# The project's managed identity pulls Hosted Agent images. Terraform owns this
# ACR assignment. The per-agent identity needs no registry role, and azd creates
# none when AZD_AGENT_SKIP_ACR=true selects the prebuilt image.
resource "azurerm_role_assignment" "foundry_project_acr_reader" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "Container Registry Repository Reader"
  principal_id         = azapi_resource.foundry_project.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Optional data-plane rights for the human or CI principal that runs
# `azd deploy`. Owner/Contributor do not include agent data actions.
resource "azurerm_role_assignment" "agent_deployer_project_manager" {
  count                = var.agent_deployer_principal_id == null ? 0 : 1
  scope                = azapi_resource.foundry_project.id
  role_definition_name = var.agent_deployer_project_role_name
  principal_id         = var.agent_deployer_principal_id
}

resource "azurerm_role_assignment" "agent_deployer_acr_push" {
  count                = var.agent_deployer_principal_id == null ? 0 : 1
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = var.agent_deployer_principal_id
}
