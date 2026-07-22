# FastAPI entry point. wird in Woche implementiert
from fastapi import FastAPI

app = FastAPI(title="Enterprise RAG API")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Service is running"}