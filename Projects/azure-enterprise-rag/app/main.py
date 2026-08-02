import os
import io
import psycopg2
from pgvector.psycopg2 import register_vector
import PyPDF2
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from openai import AzureOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter

app = FastAPI(title="Enterprise RAG API mit PostgreSQL pgvector")

# --- OPENAI SETUP ---
API_KEY = os.getenv("OPENAI_API_KEY")
ENDPOINT = os.getenv("OPENAI_ENDPOINT")

if API_KEY and ENDPOINT:
    client = AzureOpenAI(
        api_key=API_KEY,
        azure_endpoint=ENDPOINT,
        api_version="2024-08-01-preview" 
    )
else:
    client = None

# --- POSTGRESQL SETUP ---
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "postgres")

def get_db_connection():
    # SSL ist für Azure PostgreSQL zwingend erforderlich
    conn = psycopg2.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        dbname=DB_NAME,
        port=5432,
        sslmode='require'
    )
    cur = conn.cursor()
    
    # 1. Pgvector-Erweiterung in der DB aktivieren
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    
    # 2. Tabelle für Dokumente und Embeddings erstellen (1536 Dim. für text-embedding-3-small)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rag_documents (
            id bigserial PRIMARY KEY,
            filename text,
            content text,
            embedding vector(1536)
        );
    """)
    conn.commit()
    
    # 3. Vector-Datentyp in psycopg2 registrieren
    register_vector(conn)
    cur.close()
    return conn

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200, length_function=len)

class AskRequest(BaseModel):
    question: str

# --- ENDPUNKTE ---

@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    if not client:
        raise HTTPException(status_code=500, detail="OpenAI Client nicht konfiguriert.")

    # 1. Text auslesen
    if file.filename.endswith(".txt"):
        content = await file.read()
        text = content.decode("utf-8")
    elif file.filename.endswith(".pdf"):
        content = await file.read()
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))
        text = "".join(page.extract_text() or "" for page in pdf_reader.pages)
    else:
        raise HTTPException(status_code=400, detail="Nur .txt oder .pdf erlaubt.")

    # 2. Text zerstückeln
    chunks = text_splitter.split_text(text)
    if not chunks:
        return {"message": "Kein Text gefunden."}

    # 3. Embeddings via Azure OpenAI generieren
    try:
        response = client.embeddings.create(input=chunks, model="text-embedding-3-small")
        embeddings = [data.embedding for data in response.data]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding-Fehler: {str(e)}")

    # 4. In PostgreSQL speichern
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for chunk, emb in zip(chunks, embeddings):
            cur.execute(
                "INSERT INTO rag_documents (filename, content, embedding) VALUES (%s, %s, %s)",
                (file.filename, chunk, emb)
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Datenbank-Fehler: {str(e)}")

    return {"filename": file.filename, "message": f"Erfolg! {len(chunks)} Abschnitte in PostgreSQL gespeichert."}


@app.post("/ask")
async def ask_question(request: AskRequest):
    if not client:
        raise HTTPException(status_code=500, detail="OpenAI Client nicht konfiguriert.")
    
    try:
        # 1. Frage in Vektor verwandeln
        question_embedding = client.embeddings.create(
            input=request.question,
            model="text-embedding-3-small"
        ).data[0].embedding

        # 2. Ähnlichkeitssuche mit L2-Distanz-Operator (<->) in PostgreSQL
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT content FROM rag_documents 
            ORDER BY embedding <-> %s::vector 
            LIMIT 3
        """, (str(question_embedding),)) 
        
        results = cur.fetchall()
        cur.close()
        conn.close()

        # 3. Kontext aufbauen
        documents = [row[0] for row in results]
        context_text = "\n\n---\n\n".join(documents) if documents else "Keine relevanten Dokumente gefunden."

        # 4. Antwort von GPT-3.5 generieren lassen
        system_prompt = f"""Du bist ein hilfreicher Assistent. Beantworte die Frage AUSSCHLIESSLICH basierend auf folgendem Kontext.
        KONTEXT:
        {context_text}
        """

        chat_response = client.chat.completions.create(
            model="gpt-5.6-luna",  
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.question}
            ]
        )
        
        return {
            "answer": chat_response.choices[0].message.content,
            "wissen_aus_postgres": context_text 
        }

    except Exception as e:
        import traceback
        traceback.print_exc() 
        return {"WAHRER_FEHLER": str(e)}