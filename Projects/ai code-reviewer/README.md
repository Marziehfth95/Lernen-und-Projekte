# AI Code Review Bot (mit RAG & Langzeitgedächtnis)

Ein fortschrittlicher, event-gesteuerter Code-Review-Assistent, der tief in GitHub integriert ist. Dieses Projekt nutzt modernste **LLMs (Anthropic Claude)** für semantische Code-Analysen und eine **Vektordatenbank (ChromaDB)** als Langzeitgedächtnis, um Entwickler kontextbezogen auf wiederkehrende Fehler hinzuweisen.

Entwickelt, um moderne Platform Engineering, Applied AI und MLOps Praktiken zu demonstrieren.

## ✨ Kernfunktionen (Key Features)

**RAGmbasiertes Langzeitgedächtnis (Memory):** Nutzt ChromaDB, um vergangene Code-Fehler (Issues) und deren Lösungen als Vektor-Embeddings zu speichern. Bei neuen Pull Requests prüft das System historisches Wissen ab und warnt Entwickler aktiv davor, denselben Fehler zweimal zu machen.
**Enterprise-Grade Security:** Implementiert den Branchenstandard für GitHub-Authentifizierung. Anstelle von statischen Personal Access Tokens (PATs) agiert der Bot als vollwertige **GitHub App**. Die Authentifizierung erfolgt hochsicher über asymmetrische Kryptographie (`.pem` Private Keys) und dynamische JWT-Tokens.
**Event-Driven Architecture:** Ein lokaler FastAPI-Server lauscht asynchron auf GitHub Webhooks in Echtzeit. Ausgelöst durch Events wie `pull_request opened` oder `synchronize`.
**Deep Semantic Analysis:** Nutzt Anthropics Claude-3 Modelle (Haiku/Sonnet), um Code-Diffs zu analysieren, Refactoring-Vorschläge zu generieren und direkte Kommentare (mit Line-References) in den GitHub PR zu posten.

## Systemarchitektur

Der Datenfluss ist als ereignisgesteuerte Pipeline (Event-Driven Pipeline) konzipiert:

```text
[ GitHub PR Event ] ──(Webhook)──> [ FastAPI Server ]
                                          │
                                          ▼
[ GitHub App Auth ] <──(JWT/PEM)── [ PyGithub Client ] ──> Fetch PR Diff
                                          │
                                          ▼
                                   [ Claude 3 API ] ──> Semantic Code Analysis
                                          │
                                          ▼
[ ChromaDB Vektor DB ] <──(RAG)─── [ Memory Check ] ──> Ähnliche Fehler suchen?
                                          │
                                          ▼
[ GitHub PR Thread ] <──(POST)──── [ Automatischer Kommentar & Fix ]
```
## Tech Stack
Backend: Python 3.12, FastAPI, Uvicorn (ASGI)

AI / LLM: Anthropic API (Claude 3.5 Sonnet / Claude 3 Haiku)

Vektordatenbank: ChromaDB (Lokale Persistenz)

Integration: PyGithub, GitHub Apps API, Webhooks

Infrastruktur / Testing: Ngrok (Secure Tunnels), python-dotenv

## Installation & Lokales Setup
1. Repository klonen & Abhängigkeiten installieren
```bash
git clone [https://github.com/DEIN_USERNAME/ai-reviewer-test.git](https://github.com/DEIN_USERNAME/ai-reviewer-test.git)
cd ai-reviewer-test
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate  
pip install -r requirements.txt
```

2. Umgebungsvariablen konfigurieren
Erstelle eine .env Datei im Hauptverzeichnis mit folgendem Inhalt:
```Ini, TOML
GITHUB_WEBHOOK_SECRET=dein_geheimes_webhook_passwort
GITHUB_APP_ID=123456
ANTHROPIC_API_KEY=sk-ant-api03-...
```

3. GitHub App Private Key hinzufügen
Speichere den generierten RSA Private Key deiner GitHub App exakt unter dem Namen private-key.pem im Hauptverzeichnis. (Hinweis: Diese Datei ist aus Sicherheitsgründen in der .gitignore hinterlegt und wird niemals gepusht).

4. Server & Webhook starten
Starte den lokalen ASGI-Server:
```bash
uvicorn main:app --reload
```

## Future Roadmap

* Multi-Model Support: Fallback Logik implementieren (z.B. Wechsel zu OpenAI GPT-4o), falls Anthropic Rate Limits erreicht werden.

* Containerisierung: Das FastAPI Backend in ein Docker Image verpacken und eine CI/CD Pipeline für das Deployment auf AWS ECS oder Google Cloud Run aufbauen.