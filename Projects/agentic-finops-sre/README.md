#  Agentic AI & Kubernetes Self-Healing Engine

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Kubernetes](https://img.shields.io/badge/Kubernetes-Operator-326ce5?logo=kubernetes&logoColor=white)
![LangGraph](https://img.shields.io/badge/Agentic_AI-LangGraph-FF9900)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Groq](https://img.shields.io/badge/LLM-Llama_3-black)

Ein modernes  Projekt an der Schnittstelle von **Site Reliability Engineering (SRE)** und **Künstlicher Intelligenz**. Dieses Projekt demonstriert den Aufbau eines autonomen Multi Agenten Systems zur Datenanalyse, gepaart mit einem Kubernetes Operator, der Ressourcen Engpässe der KI in Echtzeit erkennt und dynamisch heilt.

##  Architektur & Kernkonzepte

Dieses System löst zwei hochkomplexe Probleme der modernen Softwareentwicklung:

1. **Self-Correcting Code (Agentic Workflow)**
   Große Datensätze zu analysieren (FinOps & E-Commerce) führt oft zu Code-Fehlern. Dieses Projekt nutzt *LangGraph*, um einen zyklischen Multi-Agenten-Workflow zu implementieren:
   - Der **Analyst-Agent** schreibt Pandas/Python-Code.
   - Der **Executor-Agent** führt den Code isoliert aus.
   - Bei Fehlern (z.B. `ValueError` bei Date-Joins) greift eine **Feedback-Loop**: Die KI analysiert den Stacktrace und repariert ihren eigenen Code völlig autonom.

2. **Self-Healing Infrastructure (Kubernetes Operator Pattern)**
   Datenanalyse-KIs sind ressourcenhungrig. In diesem Projekt betreiben wir aktives **Chaos Engineering**:
   - Die KI-Applikation wird als FastAPI-Microservice in Kubernetes (via Docker) deployt, jedoch absichtlich mit einem harten Speicherlimit von `128Mi` (RAM).
   - Beim Ausführen komplexer Data-Joins stürzt der Pod aufgrund von Speicherüberlauf (`OOMKilled`) ab.
   - Ein selbst geschriebener **Python Kubernetes Operator** überwacht den Cluster rund um die Uhr, erkennt den `CrashLoopBackOff`, diagnostiziert den Ressourcenmangel und patcht das Deployment dynamisch auf `512Mi`, um das System zu retten und die Anfrage erfolgreich abzuschließen.

## Tech Stack

- **AI & Data:** LangGraph, LangChain, Groq API (Llama-3.3-70B), Pandas, Matplotlib
- **Backend:** Python, FastAPI, Uvicorn, Docker
- **Infrastruktur & SRE:** Kubernetes (`kind`), Kubernetes Python Client

## Lokale Installation & Ausführung

### 1. Repository klonen & Setup
```bash
git clone [https://github.com/DEIN_NAME/agentic-finops-sre.git](https://github.com/DEIN_NAME/agentic-finops-sre.git)
cd agentic-finops-sre
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
### 2. API Key konfigurieren
Erstelle eine .env Datei im Hauptordner und trage deinen (kostenlosen) Groq API Key ein:

```Plaintext
GROQ_API_KEY=gsk_DeinKeyHier
```
### 3. Docker & Kubernetes Deployment
Bringe das Image in deinen lokalen Cluster und starte die "Falle":

```Bash
docker build -t finops-agent:latest .
kind load docker-image finops-agent:latest
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment.yaml
```

### 4. Das Self-Healing Spektakel auslösen
Leite den Netzwerkverkehr an den Pod weiter:

```Bash
kubectl port-forward svc/finops-service 8000:80
Sende eine Anfrage an die KI (dies wird aufgrund des 128Mi Limits zum Absturz führen):
```
```Bash
curl -X POST "http://localhost:8000/analyze" \
-H "Content-Type: application/json" \
-d '{"query": "Was war der umsatzstärkste Tag 2018 und wie hoch waren die Cloud-Kosten?"}'
```
(Die Verbindung wird abbrechen. Der Pod ist nun OOMKilled).

Starte nun den Operator, um das System zu heilen:

```Bash
python self_healing_operator.py
Der Operator wird den Absturz erkennen, das RAM-Limit auf 512Mi erhöhen und den Pod neu starten. Ein erneuter curl-Aufruf wird nun erfolgreich verarbeitet und gibt die FinOps-Management-Zusammenfassung zurück!
```
###  Use Case: FinOps & Unit Economics
Der verarbeitete Datensatz simuliert eine reale Herausforderung: Die KI mergt echte brasilianische E-Commerce-Daten (Olist) mit Cloud-Infrastruktur-Logs, um die "Cost-per-Order" und Cloud-Profitabilität an Hochlast-Tagen zu berechnen.

##  Future Roadmap & Enterprise Enhancements

Dieses Projekt dient aktuell als robuster Proof of Concept (PoC). Um das System auf ein "Enterprise Grade" Produktionsniveau zu heben, sind folgende architektonische Erweiterungen geplant:

###  Security & AI Architecture
- **Sandboxed Code Execution:** Aktuell führt der Executor Agent den Python Code im selben Container aus (`exec()`). In Produktion muss dies zwingend in isolierten, ephemeren Umgebungen (z.B. via [E2B](https://e2b.dev/) oder in dedizierten, kurzlebigen Kubernetes Pods) geschehen, um RCE Vulnerabilities (Remote Code Execution) zu verhindern.
- **Human in the Loop (HITL):** Erweiterung des LangGraph Workflows um einen Interrupt Status. Bevor der Executor Agent ressourcenintensive Datenbank-Abfragen oder Löschvorgänge ausführt, wird eine Genehmigung (Approve/Reject) via Slack-Webhook vom Data-Science-Team eingeholt.
- **LangGraph Checkpointing (Memory):** Hinzufügen einer PostgreSQL Datenbank, damit der Analyst Agent sich Schema Fehler vergangener Analysen merkt und denselben Code Fehler in zukünftigen Sessions nicht wiederholt.

### Site Reliability Engineering (SRE) & Observability
-  **Prometheus & Grafana Integration:** Der Self Healing Operator wird so erweitert, dass er Custom Metrics exportiert. Ein Grafana Dashboard soll dann KPIs wie *Anzahl der OOMKills*, *Time to Heal (TTH)* und *Erfolgsquote der KI-Agenten* visualisieren.
- **Predictive Scaling (AIOps):** Anstatt erst nach dem Absturz (`OOMKilled`) zu reagieren, soll der Operator die Größe des Payloads (oder der CSV-Dateien) im Vorfeld analysieren und das Deployment *präventiv* skalieren, um den Absturz vollständig zu vermeiden.
- **Helm Chart Deployment:** Paketierung der gesamten Architektur (FastAPI, Operator, RBAC-Rollen, ConfigMaps) in ein sauberes Helm Chart für 1-Klick-Deployments in beliebigen Clustern.

### FinOps Data
- [ ] **Live API Integration:** Ersetzen der synthetischen `cloud_billing.csv` durch die echte AWS Cost Explorer API oder [Kubecost](https://www.kubecost.com/), um reale, minütliche Cloud Infrastrukturkosten auszuwerten.