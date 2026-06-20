# AIOps: Predictive Self Healing Kubernetes Engine

Ein KI gestütztes, prädiktives Site Reliability Engineering (SRE) Tool, das Kubernetes Cluster autonom überwacht, Anomalien mittels Machine Learning erkennt und fehlerhafte Deployments proaktiv heilt, bevor sie Systemausfälle verursachen. 

## Übersicht

Moderne Cloud-Native Umgebungen erfordern mehr als nur reaktive Health Checks. 
Dieses Projekt erweitert traditionelle Kubernetes Selbstheilungsmechanismen durch die Integration von **Observability (Prometheus/Grafana)** und **Machine Learning (Scikit-Learn)**. 

Anstatt darauf zu warten, dass ein Pod wiederholt abstürzt (`CrashLoopBackOff`), analysiert diese Engine kontinuierlich Live Metriken und nutzt ein **Isolation Forest**-Anomalieerkennungsmodell. 
So werden Instabilitäten vorhergesagt und Deployments automatisch auf einen stabilen Zustand zurückgerollt. 

### Hauptfunktionen
**Prädiktive Anomalieerkennung:** Nutzt ein trainiertes Machine Learning Modell, um Live-Prometheus-Metriken auszuwerten und ungewöhnliche Neustart-Muster zu erkennen.
**Proaktive Fehlerbehebung:** Kommuniziert direkt mit der Kubernetes-API, um Deployments autonom zu patchen und zurückzurollen. 
**Observability Stack:** Vollständig integriert mit dem `kube-prometheus-stack` für Echtzeit-Metrik-Scraping und Grafana-Dashboards. 
**Chaos Engineering Ready:** Beinhaltet eine beispielhafte Python/Flask "Victim App" mit einem `/crash`-Endpoint, um Ausfälle für das Modell-Training und Tests zu simulieren. [cite: 106, 107]

---

## Architektur

1. **Das Opfer (Victim App):** Ein leichtgewichtiger Python Webservice, der in Kubernetes läuft. Er bietet einen `/crash`-Endpoint, um kritische Fehler (HTTP 500) zu simulieren. 
2. **Das Nervensystem (Observability):** Prometheus überwacht kontinuierlich den Cluster und trackt die Metrik `kube_pod_container_status_restarts_total`. 
3. **Das Gehirn (AIOps Operator):** Eine maßgeschneiderte Python-Engine. Sie ruft alle 10 Sekunden Live Metriken über die Prometheus-API ab. 
4. **Der Heilungs-Loop:** Die Live Daten werden in ein vortrainiertes **Isolation Forest ML-Modell** eingespeist. 
Sagt das Modell eine Anomalie voraus, nutzt der Operator die K8s-API, um das Deployment automatisch auf ein stabiles Image (`v1`) zurückzurollen, ganz ohne manuellen Eingriff. [cite: 608, 644]

---

## Tech Stack

**Infrastruktur:** Kubernetes (Kind), Docker, Helm 
**Observability:** Prometheus, Grafana, Alertmanager 
**AIOps / Machine Learning:** Python, Scikit-Learn (Isolation Forest), Pandas, Joblib 
**Orchestrierung:** Kubernetes Python Client 

---

## Lokales Setup & Reproduktion

Möchtest du diese KI-Engine lokal ausführen? Folge diesen Schritten:

### 1. Cluster & Observability Stack starten
```bash
# Lokalen Kubernetes Cluster mit Kind starten
kind create cluster --name self-healing-cluster

# Prometheus & Grafana via Helm installieren
helm repo add prometheus-community [https://prometheus-community.github.io/helm-charts](https://prometheus-community.github.io/helm-charts)
helm install prometheus prometheus-community/kube-prometheus-stack -n monitoring --create-namespace
```
### 2. Victim App deployen
```bash
# Die verwundbare App bauen und in den Cluster laden
cd app
docker build -t victim-app:v1 .
kind load docker-image victim-app:v1 --name self-healing-cluster

# Kubernetes-Manifeste anwenden
cd ../k8s
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```
### 3. Prädiktive KI Engine starten
```bash
# Den Prometheus-Port weiterleiten, damit die KI Live-Metriken lesen kann
kubectl port-forward svc/prometheus-kube-prometheus-prometheus 9090:9090 -n monitoring &

# Den Python Operator starten
cd ../operator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```
### 4. Chaos auslösen & KI bei der Heilung beobachten
```bash
# Die App erreichbar machen
kubectl port-forward svc/victim-app-svc 8080:80 &

# Den Crash auslösen
curl -X POST http://localhost:8080/crash
```
## Zukünftige Erweiterungen
Integration von GenAI (LLM), um nach einem Absturz automatisch K8s-Event Logs abzurufen und eine Zusammenfassung der Root Cause Analysis (RCA) an Slack zu senden.