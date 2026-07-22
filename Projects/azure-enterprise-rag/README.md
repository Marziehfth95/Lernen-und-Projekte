# Enterprise-Ready RAG Platform on Azure

This repository contains an end-to-end containerized Retrieval-Augmented Generation (RAG) service.

## Architecture
- **Backend:** FastAPI (Python)
- **Infrastructure as Code:** Terraform
- **Cloud Provider:** Microsoft Azure
- **Compute:** Azure Container Apps (Serverless)
- **Database:** PostgreSQL Flexible Server with `pgvector`
- **AI Models:** Azure OpenAI (Embeddings & Chat Completions)
- **Security:** Azure Key Vault & Managed Identities
- **CI/CD:** GitHub Actions

## Roadmap
- Phase 1: Terraform Foundation & Remote State
- Phase 2: Containerization & Cloud Deployment
- Phase 3: Database setup & Azure OpenAI Integration
- Phase 4: CI/CD Pipeline & Observability

*Note: This project is currently under construction.*