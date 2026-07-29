# Enterprise Serverless RAG API on Azure

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688)
![Terraform](https://img.shields.io/badge/Terraform-1.5%2B-5C4EE5)
![Azure](https://img.shields.io/badge/Azure-Container_Apps-0078D4)

An end-to-end containerized **Retrieval-Augmented Generation (RAG)** microservice, deployed entirely via Infrastructure as Code (IaC) on Microsoft Azure. 

This API allows users to upload documents (PDF, TXT), converts the text into vector embeddings, and enables semantic search to answer user queries using natural language. The architecture is designed to be serverless, scaling automatically while keeping infrastructure costs minimal.

## Architecture & Tech Stack

- **Backend Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Python) for high-performance, asynchronous REST endpoints.
- **AI Models:** Azure OpenAI 
  - Embeddings: `text-embedding-3-small`
  - Chat/Completion: `gpt-35-turbo`
- **Vector Database:** [ChromaDB](https://www.trychroma.com/) (embedded locally for fast, in-memory retrieval).
- **Document Processing:** `PyPDF2` and `langchain-text-splitters` for intelligent chunking.
- **Infrastructure as Code:** HashiCorp Terraform.
- **Cloud Hosting:** Azure Container Apps & Azure Container Registry (ACR).

## Key Features

1. **Document Ingestion (`POST /upload`):** 
   - Accepts `.txt` and `.pdf` files.
   - Extracts and splits text into logical chunks (1000 characters with 200 overlap).
   - Generates high-quality vector embeddings via Azure OpenAI.
   - Indexes the data in ChromaDB.
2. **Context-Aware Q&A (`POST /ask`):** 
   - Converts user queries into vector embeddings.
   - Retrieves the top-3 most semantically relevant document chunks.
   - Synthesizes a precise, context-bound answer using GPT-3.5.
3. **Fully Automated Deployment:** 
   - The entire cloud infrastructure can be spun up (`terraform apply`) and completely destroyed (`terraform destroy`) in minutes.

---

##  Getting Started

### Prerequisites
- [Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli) installed and authenticated (`az login`).
- [Terraform](https://developer.hashicorp.com/terraform/downloads) installed.
- [Docker](https://www.docker.com/) installed.
- An active Azure Subscription with access to Azure OpenAI.

### 1. Infrastructure Provisioning (Terraform)
Navigate to the `terraform` directory to create the Azure Container Registry, Azure OpenAI instance, and Container App Environment.

```bash
cd terraform
terraform init
terraform apply
```

### 2. Build & Deploy the API
Navigate to the app directory to build the Docker image and push it to the newly created Azure Container Registry.
```bash
cd app

# Login to your Azure Container Registry
az acr login --name <acr_name>

# Build the Docker image
docker build --platform linux/amd64 -t <your_acr_name>.azurecr.io/rag-app:v1 .

# Push the image to Azure
docker push <your_acr_name>.azurecr.io/rag-app:v1
```

## API Endpoints (Swagger UI)

Once deployed, the FastAPI interactive documentation is automatically generated and accessible at `https://<your-container-app-url>/docs`.

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/upload` | `POST` | Uploads a `.pdf` or `.txt` file, chunks it, and stores embeddings. |
| `/ask` | `POST` | Accepts a JSON payload `{"question": "..."}` and returns an AI-generated answer based on uploaded documents. |


## Future Enhancements (Roadmap)
This project currently serves as a highly functional MVP. To scale this architecture for enterprise-grade production, the following enhancements are planned:

* Persistent & Managed Vector Database: Migration from local ChromaDB to a managed, distributed vector store like PostgreSQL with pgvector or Azure AI Search. This will decouple storage from compute and ensure data persistence across container restarts.

* Interactive Frontend: Development of a user-friendly Web UI using Streamlit or React to allow non-technical users to interact with the RAG system easily.

* CI/CD Pipeline Integration: Implementation of GitHub Actions workflows to automatically lint code, build Docker images, and apply Terraform configurations upon merging to the main branch.

* Enterprise Security: Integration with Azure Entra ID (Active Directory) for robust endpoint protection and Role-Based Access Control (RBAC).

* Advanced RAG Techniques: Implementation of hybrid search (Keyword + Semantic) and query re-ranking to further improve the accuracy of retrieved context.