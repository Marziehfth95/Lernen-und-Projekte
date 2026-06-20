# Leichtgewichtiges Python Basis Image
FROM python:3.9-slim

# Arbeitsverzeichnis im Container
WORKDIR /app

# Abhängigkeiten kopieren und installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Den restlichen Code kopieren
COPY main.py .

# Port freigeben, auf dem die Flask App läuft
EXPOSE 8080

# Befehl zum Starten der App
CMD ["python", "main.py"]