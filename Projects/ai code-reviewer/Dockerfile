#Da Cloud Run "zustandslos" (stateless) ist, wird der Ordner chroma_db bei jedem Neustart des Containers geleert.
#Für ein echtes Produktionssystem müsste ich in der Cloud später einen "Persistent Volume Claim" oder einen Cloud Storage Bucket für ChromaDB anhängen. Für das Portfolio reicht dieser Hinweis völlig aus


# Nutze ein schlankes Python-Image
FROM python:3.12-slim

# Setze das Arbeitsverzeichnis im Container
WORKDIR /app

# Kopiere die Abhängigkeiten und installiere sie
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere den gesamten restlichen Code
COPY . .

# Exponiere Port 8080 (Standard für Google Cloud Run)
EXPOSE 8080

# Starte den Server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]