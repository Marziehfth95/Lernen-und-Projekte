variable "db_password" {
  description = "Das Passwort für PostgreSQL (Muss komplex sein: Groß/Klein/Zahlen/Sonderzeichen)"
  type        = string
  sensitive   = true
}