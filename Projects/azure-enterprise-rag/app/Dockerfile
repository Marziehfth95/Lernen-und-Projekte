# Offizielles, leichtgewichtiges Python-Image nutzen
FROM python:3.11-slim

# Arbeitsverzeichnis im Container festlegen
WORKDIR /app

# Umgebungsvariablen setzen
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System-Updates
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Requirements installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Den eigentlichen Code kopieren
COPY . .

# Container nicht als root ausführen
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Den Port freigeben
EXPOSE 8000

# Den Server starten
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
