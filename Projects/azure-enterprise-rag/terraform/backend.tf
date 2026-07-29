terraform {
  backend "azurerm" {
    resource_group_name  = "rg-terraform-state"
    storage_account_name = "tfstatefattahi995"
    container_name       = "tfstate"
    key                  = "terraform.tfstate"
  }
}