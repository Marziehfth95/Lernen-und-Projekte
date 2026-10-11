variable "db_password" {
  description = "Das Passwort für PostgreSQL (Muss komplex sein: Groß/Klein/Zahlen/Sonderzeichen)"
  type        = string
  sensitive   = true
}

variable "auth_tenant_id" {
  description = "Microsoft Entra tenant ID whose access tokens are accepted by the API"
  type        = string
}

variable "auth_client_id" {
  description = "Application (client) ID exposed as the API audience"
  type        = string
}

variable "auth_required_role" {
  description = "Optional Entra application role required by the API"
  type        = string
  default     = ""
}

variable "auth_required_scope" {
  description = "Optional delegated OAuth scope required by the API"
  type        = string
  default     = "access_as_user"
}

variable "external_ingress_enabled" {
  description = "Expose the Container App publicly. Keep false for private enterprise deployments."
  type        = bool
  default     = false
}
