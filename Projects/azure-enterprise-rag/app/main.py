import os
import io
import chromadb
import PyPDF2
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from openai import AzureOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter

app = FastAPI(title="Azure RAG API mit ChromaDB")

# Open AI Setup
API_KEY = os.getenv("OPENAI_API_KEY")
ENDPOINT = os.getenv("OPENAI_ENDPOINT")

if API_KEY and ENDPOINT:
    client = AzureOpenAI(
        api_key=API_KEY,
        azure_endpoint=ENDPOINT,
        api_version="2024-02-15-preview"
    )
else:
    client = None

# Chroma DB set up
# Wir speichern die Daten lokal im Container
chroma_client = chromadb.PersistentClient(path="./chroma_data")
collection = chroma_client.get_or_create_collection(name="rag_documents")

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200, length_function=len)

class AskRequest(BaseModel):
    question: str

#Endpunkt

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

    # 4. In ChromaDB speichern
    ids = [f"{file.filename}_{i}" for i in range(len(chunks))]
    collection.add(
        embeddings=embeddings,
        documents=chunks,
        ids=ids
    )

    return {"filename": file.filename, "message": f"Erfolg! {len(chunks)} Abschnitte in ChromaDB gespeichert."}


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

        # 2. Suche in ChromaDB
        results = collection.query(
            query_embeddings=[question_embedding],
            n_results=3
        )
        
        # 3. Kontext zusammenbauen
        documents = results["documents"][0] if results["documents"] else []
        context_text = "\n\n---\n\n".join(documents) if documents else "Keine relevanten Dokumente gefunden."

        # 4. KI antworten lassen
        system_prompt = f"""Du bist ein hilfreicher Assistent. Beantworte die Frage AUSSCHLIESSLICH basierend auf folgendem Kontext.
        KONTEXT:
        {context_text}
        """

        chat_response = client.chat.completions.create(
            model="gpt-35-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.question}
            ]
        )
        
        return {
            "answer": chat_response.choices[0].message.content,
            "wissen_aus_chroma": context_text 
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))