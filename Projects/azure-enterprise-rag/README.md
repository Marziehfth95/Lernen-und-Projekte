# 🚀 Enterprise RAG-Service auf Azure (Terraform, pgvector & FastAPI)

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Terraform](https://img.shields.io/badge/Terraform-IaC-623CE4.svg?style=for-the-badge&logo=terraform&logoColor=white)](https://www.terraform.io/)
[![Azure](https://img.shields.io/badge/Azure-Cloud-0089D6.svg?style=for-the-badge&logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-336791.svg?style=for-the-badge&logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![CI/CD](https://img.shields.io/badge/GitHub_Actions-Automated-2088FF.svg?style=for-the-badge&logo=githubactions&logoColor=white)](https://github.com/features/actions)

Ein vollautomatisierter, cloud-nativer **Retrieval-Augmented Generation (RAG)** Service. Dieses Projekt demonstriert den Aufbau eines produktionsreifen KI-Backends, das Unternehmensdaten (PDFs/TXTs) sicher speichert, durchsuchbar macht und mit modernsten Azure OpenAI Modellen (`gpt-5.6-luna`) intelligente Antworten generiert. 

Die gesamte Infrastruktur ist als **Infrastructure as Code (IaC)** über Terraform deklariert und wird per **CI/CD-Pipeline** in Azure Container Apps bereitgestellt.

---

##  Kern-Features

- **End-to-End Infrastructure as Code:** Vollständige Provisionierung der Azure-Ressourcen (Ressourcengruppe, Container Registry, Container Apps, PostgreSQL Flexible Server) via Terraform.
- **Enterprise Vector Search:** Nutzung von `pgvector` als Vektordatenbank direkt in PostgreSQL für nahtlose L2-Distanz-Suche (`<->`) ohne zusätzliche proprietäre Vektor-DBs. Dies entkoppelt den Speicher vom Compute-Node und garantiert Persistenz.
- **State-of-the-Art KI-Modelle:** Integration der neuesten Azure OpenAI Modelle (`text-embedding-3-small` für Embeddings, `gpt-5.6-luna` für Chat-Completions).
- **Serverless & Skalierbar:** Deployment auf Azure Container Apps (Skalierung bis auf Null) für maximale Kosteneffizienz.
- **Vollautomatisches CI/CD:** GitHub Actions Pipeline für Build, Push (ACR) und Deploy.

---

## Architektur

```mermaid
graph TD
    A[Client / Swagger UI] -->|REST API| B(FastAPI Backend Container)
    
    subgraph Azure Cloud
        B -->|1. Zerstückeln & Vektorisieren| C[Azure OpenAI: text-embedding-3-small]
        B -->|2. Speichern / L2-Suche| D[(PostgreSQL Flexible Server + pgvector)]
        B -->|3. Kontext + Frage| E[Azure OpenAI: gpt-5.6-luna]
    end
    
    E -->|Generierte Antwort| B
    B -->|JSON Response| A

```
##  Tech-Stack

| Komponente | Technologie | Beschreibung |
| :--- | :--- | :--- |
| **Backend API** | FastAPI, Python, Uvicorn | Asynchrones Framework für extrem schnelle API-Antworten. |
| **Infrastruktur** | Terraform, Azure CLI | Deklaratives IaC für reproduzierbare Azure-Umgebungen. |
| **Datenbank** | PostgreSQL Flexible Server | Relationale DB erweitert um `pgvector` (1536 Dimensionen). |
| **KI & LLM** | Azure OpenAI, LangChain | Enterprise-Grade KI (Datenschutzkonform). |
| **Deployment** | Azure Container Apps, ACR | Serverless Container Hosting. |
| **CI/CD** | GitHub Actions | Automatisierte Build- & Deployment-Workflows. |

## Projektstruktur
```bash
├── app/
│   ├── main.py              # FastAPI Anwendung (Upload & Ask Endpunkte)
│   ├── requirements.txt     # Python Abhängigkeiten
│   ├── Dockerfile           # Multi-Stage Docker Build
│   └── .env.example         # Template für Umgebungsvariablen
├── terraform/
│   ├── main.tf              # Haupt-Infrastruktur-Definitionen
│   ├── variables.tf         # Terraform Variablen (z.B. Passwörter)
│   └── outputs.tf           # Endpoints und DB-Hostnamen
├── .github/
│   └── workflows/
│       └── deploy.yml       # GitHub Actions CI/CD Pipeline
└── README.md
```

## Lokales Setup (Development)
1. Voraussetzungen
Python 3.12+ installiert

Azure CLI (az login erfolgreich durchgeführt)

Ein bereitgestellter Azure PostgreSQL Server & Azure OpenAI Account (via Terraform).

2. Installation
Klonen Sie das Repository und aktivieren Sie eine virtuelle Umgebung:
```bash
git clone [https://github.com/IhrUsername/azure-enterprise-rag.git](https://github.com/IhrUsername/azure-enterprise-rag.git)
cd azure-enterprise-rag

python -m venv .venv
# Mac/Linux
source .venv/bin/activate  
# Windows
# .venv\\Scripts\\activate  

pip install -r app/requirements.txt
```

3. Umgebungsvariablen konfigurieren
Erstellen Sie im Ordner app/ eine .env Datei:
```Ini, TOML
OPENAI_API_KEY="azure_openai_key"
OPENAI_ENDPOINT="https://<ihre-ressource>[.openai.azure.com/](https://.openai.azure.com/)"
DB_HOST="<ihr-server>.postgres.database.azure.com"
DB_USER="psqladmin"
DB_PASS="SicheresPasswort123!"
DB_NAME="postgres"
```
4. Server starten
```bash
cd app
uvicorn main:app --reload --env-file .env
```

Die interaktive API-Dokumentation (Swagger UI) ist unter http://127.0.0.1:8000/docs erreichbar.

## Infrastruktur Deployment (Terraform)
Die gesamte Umgebung kann in wenigen Minuten provisioniert werden:
```bash
cd terraform
terraform init
terraform plan
terraform apply
```

## API Endpunkte (Nutzung)
1. Dokument hochladen (POST /upload)
Liest PDF- oder TXT-Dateien aus, zerteilt sie in Chunks, generiert Embeddings und speichert sie in der DB.

cURL Beispiel:
```bash
curl -X 'POST' \
  '[http://127.0.0.1:8000/upload](http://127.0.0.1:8000/upload)' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file=@mein_dokument.pdf'
  ```
  2. Frage stellen (POST /ask)
    Sucht den relevantesten Kontext per Vektor-Distanz aus der Datenbank und lässt GPT eine fundierte Antwort generieren.

Request Body:
```JSON
{
  "question": "Was sind die wichtigsten Qualifikationen im Lebenslauf?"
}
```
Respons:
```JSON
{
  "answer": "Basierend auf dem Kontext verfügt der Bewerber über tiefgehende Expertise in Cloud Architecture, Kubernetes, und Python-Entwicklung...",
  "wissen_aus_postgres": "[Gefundene Text Chunks ]"
}
```
## Security & Compliance
Private Endpoints: Azure OpenAI behält keine Prompts zu Trainingszwecken (Enterprise Privacy).

Secrets Management: Passwörter und API-Keys werden über Azure Key Vault / GitHub Secrets injiziert.

SSL-Verschlüsselung: Die PostgreSQL-Datenbank erzwingt sslmode='require'.

Entwickelt als Teil eines professionellen Cloud- & AI-Engineering Portfolios.

## 🗺 Future Enhancements (Roadmap)

Dieses Projekt dient als voll funktionsfähiges MVP. Um die Architektur für hochskalierende Produktionsumgebungen zu erweitern, sind folgende Ausbaustufen geplant:

- **Hybrid Search & Re-Ranking:** Kombination aus Keyword Suche (BM25) und semantischer Vektorsuche sowie Integration eines Cross-Encoders (z.B. Cohere) für präzisere Suchergebnisse.
- **Enterprise Security (RBAC):** Absicherung der FastAPI Endpunkte durch **Azure Entra ID (Active Directory)** mit OAuth2 für rollenbasierte Zugriffskontrolle.
- **Observability & LLM-Tracing:** Integration von **Azure Application Insights** oder LangSmith, um Token-Verbrauch, Latenzen und LLM-Halluzinationen in Echtzeit zu überwachen.
- **Interaktives Frontend:** Entwicklung eines Chat-Interfaces mit **Streamlit** oder React/Next.js, um Fachanwendern eine intuitive Bedienung ohne API-Kenntnisse zu ermöglichen.
- **Multi-Modal RAG:** Erweiterung der Datenextraktion auf Bilder und Tabellen innerhalb von PDFs mithilfe von Vision-Modellen.



