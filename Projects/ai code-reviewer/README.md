# AI Code Review Agent (RAG Memory, Self-Healing & MCP)

Ein fortschrittlicher, event gesteuerter AI Agent, der tief in GitHub integriert ist. Dieses Projekt nutzt modernste **LLMs (Anthropic Claude)** für semantische Code Analysen, eine **Vektordatenbank (ChromaDB)** als Langzeitgedächtnis und **Auto Remediation (Self Healing)**, um fehlerhaften Code automatisch zu reparieren. 

Zusätzlich bietet das System eine **MCP (Model Context Protocol)** Schnittstelle, über die externe KI Clients (wie Claude Desktop) das konsolidierte Wissen des Teams abfragen können.

Entwickelt, um moderne Platform Engineering, MLOps und Agentic AI Praktiken zu demonstrieren.

## ✨ Kernfunktionen (Key Features)

* 🛠️ **Self Healing Deployments (Auto-Fixing):** Der Bot belässt es nicht bei Kommentaren. Generiert die KI einen sicheren Fix für eine Schwachstelle (z. B. SQL Injection), nutzt das System die GitHub API, um den fehlerhaften Code automatisch zu ersetzen und als neuen Commit in den Branch zu pushen.
**RAG & Memory Consolidation (Nightly Cron):** Nutzt ChromaDB, um vergangene Code Fehler als Vektor Embeddings zu speichern. Ein nächtlicher GitHub Actions Cron-Job ("Dreaming Phase") fasst ähnliche Fehler über LLMs zu übergeordneten Meta Regeln zusammen, um die Datenbank effizient und klein zu halten.
**Model Context Protocol (MCP) Server:** Stellt das lokale Wissen der Vektordatenbank über das neue MCP-Protokoll von Anthropic bereit. Tech Leads können über ihre lokale Claude Desktop App direkt abfragen: *"Welche Code Fehler hat das Team diese Woche am häufigsten gemacht?"*
**Cloud-Native & CI/CD:** Das Webhook Backend ist vollständig containerisiert (Docker) und wird über eine GitHub Actions CI/CD-Pipeline automatisiert als zustandsloser Container auf **Google Cloud Run** deployt.
**Enterprise Grade Security:** Der Bot agiert als vollwertige **GitHub App** (kein PAT). Die Authentifizierung erfolgt hochsicher über asymmetrische Kryptographie (`.pem` Private Keys) und dynamische JWT-Tokens.

##  Systemarchitektur

Das System besteht aus drei verteilten Hauptkomponenten:

```text
1. THE WEBHOOK AGENT (Push) - Läuft in der Cloud
[ GitHub PR Event ] ──(Webhook)──> [ FastAPI Server (Cloud Run) ]
                                          │
                                   [ Claude 3.5 Sonnet ] ──> Semantic Analysis & Fix
                                          │
[ GitHub PR Thread ] <──(GIT PUSH)─[ Auto-Commit: Fehlerhafter Code wird ersetzt ]

2. THE NIGHTLY CONSOLIDATION (Cron) - Läuft via GitHub Actions
[ 03:00 AM Trigger ] ──(Auth)──> [ /system/dream Endpoint ]
                                          │
[ ChromaDB Vektor DB ] <──(LLM)─── [ Komprimiert Fehler zu Meta-Regeln ]

3. THE MCP SERVER (Pull) - Läuft lokal für Entwickler
[ Claude Desktop App ] ──(stdio)──> [ MCP Server (mcp_server.py) ]
                                          │
[ Tech-Lead Prompt ] <──(JSON)───── [ ChromaDB Read ] (Gibt Team-Fehler aus)
```
## Tech Stack
Backend & API: Python 3.12, FastAPI, Uvicorn, MCP SDK

AI / LLM: Anthropic API (Claude 3.5 Sonnet)

Vektordatenbank (RAG): ChromaDB

DevOps & Cloud: Docker, Google Cloud Run, GitHub Actions (CI/CD, Cron)

Integration: PyGithub, GitHub Apps API, Webhooks, Ngrok

## Installation & Lokales Setup
1. Repository klonen & Abhängigkeiten installieren
```bash
git clone [https://github.com/DEIN_USERNAME/ai-reviewer-test.git](https://github.com/DEIN_USERNAME/ai-reviewer-test.git)
cd ai-reviewer-test
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

2. Umgebungsvariablen konfigurieren
Erstelle eine .env Datei im Hauptverzeichnis:

```Ini, TOML
GITHUB_WEBHOOK_SECRET=dein_geheimes_webhook_passwort
GITHUB_APP_ID=123456
ANTHROPIC_API_KEY=sk-ant-api03-...
CRON_SECRET=super-geheimes-passwort-fuer-memory-consolidation
```

3. Server starten (Webhooks & RAG)
Starte den lokalen ASGI-Server und den Ngrok-Tunnel:

```Bash
uvicorn main:app --reload
ngrok http 8000
```
4. MCP Server in Claude Desktop integrieren
Füge diesen Block zu deiner claude_desktop_config.json hinzu, um Claude Zugriff auf das Team-Gedächtnis zu geben:

```JSON
{
  "mcpServers": {
    "team-memory-db": {
      "command": "/absoluter/pfad/zu/deinem/.venv/bin/python",
      "args": [
        "/absoluter/pfad/zu/deinem/mcp_server.py"
      ]
    }
  }
}
```
## Future Roadmap
* Multi-Model Fallback: Fallback-Logik implementieren (Wechsel zu OpenAI GPT-4o), falls Anthropic Rate-Limits erreicht werden.

* Metrics Dashboard: Anbindung an Prometheus & Grafana, um zu tracken, wie viele Bugs das System erfolgreich repariert hat.

* Slack/Teams Integration: Echtzeit-Benachrichtigungen an Entwicklerteams über neu gelernte Meta-Regeln aus der nächtlichen Traumphase.