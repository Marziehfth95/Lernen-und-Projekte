output "workflow_url" {
  value = var.deploy_workflow ? "https://${azurerm_container_app.workflow[0].ingress[0].fqdn}" : null
}

output "workflow_identity_client_id" {
  value = azurerm_user_assigned_identity.workflow.client_id
}

output "container_registry" {
  value = azurerm_container_registry.main.login_server
}

output "container_registry_id" {
  value = azurerm_container_registry.main.id
}

output "foundry_project_endpoint" {
  description = "Value for azd AZURE_AI_PROJECT_ENDPOINT."
  value       = "https://${local.foundry_account_name}.services.ai.azure.com/api/projects/${local.foundry_project_name}"
}

output "foundry_project_principal_id" {
  value = azapi_resource.foundry_project.identity[0].principal_id
}

output "model_deployment_name" {
  description = "Value for azd MICROSOFT_FOUNDRY_MODEL_DEPLOYMENT_NAME."
  value       = azapi_resource.model_deployment.name
}

output "aggregate_api_scope" {
  description = "Value for azd AGGREGATE_API_SCOPE."
  value       = "${var.entra_api_audience}/.default"
}

output "database_server_fqdn" {
  value = one(azurerm_postgresql_flexible_server.workflow[*].fqdn)
}

output "workflow_persistence" {
  description = "`ephemeral_sqlite_demo` means workflow records are not durable."
  value       = var.workflow_persistence
}

output "database_admin_identity" {
  value = {
    client_id    = azurerm_user_assigned_identity.database_admin.client_id
    principal_id = azurerm_user_assigned_identity.database_admin.principal_id
    name         = azurerm_user_assigned_identity.database_admin.name
  }
}

output "migration_identity" {
  value = {
    client_id    = azurerm_user_assigned_identity.migration.client_id
    principal_id = azurerm_user_assigned_identity.migration.principal_id
    name         = azurerm_user_assigned_identity.migration.name
  }
}
