from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import sys

# Füge den Ordner zum Pfad hinzu, damit wir finops_agent importieren können
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from agents.finops_agent import finops_ai_app

# 🌟 NEU: Importiere deinen Live-Data-Fetcher
from live_data_fetcher import fetch_live_kubecost

app = FastAPI(title="Agentic FinOps API", version="1.0")

class QueryRequest(BaseModel):
    query: str

@app.get("/health")
def health_check():
    """Wird von Kubernetes genutzt (Liveness/Readiness Probe)."""
    return {"status": "healthy"}

@app.post("/analyze")
def analyze_data(request: QueryRequest):
    """Triggert den LangGraph Agenten-Loop."""
    try:
        # 🌟 NEU: Bevor die KI startet, ziehen wir die absolut neuesten Daten!
        print("🔄 Lade neueste Infrastruktur-Kosten von Kubecost...")
        fetch_live_kubecost()
        
        print(f"🚀 Starte Analyse für: {request.query}")
        final_state = finops_ai_app.invoke({"query": request.query, "iterations": 0})
        
        return {
            "status": "success",
            "insights": final_state.get("insights", "Keine Insights generiert.")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/crash")
def simulate_crash():
    """Chaos-Engineering: Simuliert einen fatalen Absturz."""
    print("💥 CRASH INITIATED: Simuliere fatalen App-Absturz (Out of Memory / Kernel Panic)!")
    os._exit(1)