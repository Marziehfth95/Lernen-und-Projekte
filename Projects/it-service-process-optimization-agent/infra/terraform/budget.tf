# Early-warning budget for every resource in the demo resource group.
# Azure budgets only notify; they never stop resources or cap spending, and
# cost data lags by up to a day.
# Amounts are pre-tax in billing currency.
resource "azurerm_consumption_budget_resource_group" "demo" {
  name              = "budget-${local.suffix}"
  resource_group_id = azurerm_resource_group.main.id
  amount            = var.budget_amount
  time_grain        = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  dynamic "notification" {
    for_each = toset([20, 40, 60, 80])
    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThanOrEqualTo"
      threshold_type = "Actual"
      contact_emails = var.budget_contact_emails
    }
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Forecasted"
    contact_emails = var.budget_contact_emails
  }
}
