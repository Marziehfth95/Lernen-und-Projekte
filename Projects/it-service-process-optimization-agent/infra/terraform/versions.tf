terraform {
  required_version = ">= 1.8.0, < 2.0.0"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = ">= 2.4.0, < 3.0.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.20.0, < 5.0.0"
    }
  }
}

# Never register resource providers implicitly: registration changes
# subscription state, even during `terraform plan`. Register the required
# namespaces explicitly after approval (see the readiness report).
provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
}

provider "azapi" {
  skip_provider_registration = true
}
