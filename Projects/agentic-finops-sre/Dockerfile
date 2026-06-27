# Wir nutzen ein schlankes Python-Image
FROM python:3.11-slim

# Arbeitsverzeichnis im Container
WORKDIR /app

# Kopiere die Abhängigkeiten und installiere sie
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere unseren Code, die CSV-Daten und die .env Datei
COPY agents/ agents/
COPY data/ data/
COPY api.py .
COPY .env .

# Port freigeben, auf dem FastAPI läuft
EXPOSE 8000

# Startbefehl für den Webserver
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]