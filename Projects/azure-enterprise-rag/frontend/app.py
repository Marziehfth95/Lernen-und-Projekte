import streamlit as st
import requests

# Die URL zu deinem lokalen FastAPI Backend
API_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="Enterprise RAG", page_icon="🤖", layout="centered")

st.title("🤖 Enterprise RAG Assistant")
st.markdown("Lade ein Dokument hoch und stelle Fragen dazu. Die KI nutzt `gpt-5.6-luna` und sucht in PostgreSQL (`pgvector`).")

# Session State für die Chat-Historie initiieren
if "messages" not in st.session_state:
    st.session_state.messages = []

# --- SIDEBAR: DATEI UPLOAD ---
with st.sidebar:
    st.header("📄 Dokument-Upload")
    uploaded_file = st.file_uploader("PDF oder TXT hochladen", type=["pdf", "txt"])
    
    if st.button("Hochladen & Vektorisieren") and uploaded_file:
        with st.spinner("Dokument wird verarbeitet..."):
            # Datei an das FastAPI Backend schicken
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
            response = requests.post(f"{API_URL}/upload", files=files)
            
            if response.status_code == 200:
                st.success(response.json().get("message", "Erfolgreich hochgeladen!"))
            else:
                st.error(f"Fehler beim Upload: {response.text}")

# --- MAIN AREA: CHAT ---
# Bisherige Chat-Nachrichten anzeigen
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Eingabefeld für neue Fragen
if prompt := st.chat_input("Stelle eine Frage zu deinem Dokument..."):
    # 1. Frage des Users anzeigen und speichern
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. Antwort vom Backend holen
    with st.chat_message("assistant"):
        with st.spinner("GPT-5.6 analysiert deine Vektoren..."):
            try:
                response = requests.post(f"{API_URL}/ask", json={"question": prompt})
                
                if response.status_code == 200:
                    data = response.json()
                    answer = data.get("answer", "Keine Antwort erhalten.")
                    context = data.get("wissen_aus_postgres", "")
                    
                    # Antwort anzeigen
                    st.markdown(answer)
                    
                    # Kontext in einem Expander verstecken (Super für Demos!)
                    with st.expander("🔍 Gefundenes Wissen aus PostgreSQL anzeigen"):
                        st.info(context)
                        
                    # Antwort in die Historie speichern
                    st.session_state.messages.append({"role": "assistant", "content": answer})
                else:
                    st.error(f"API Fehler: {response.status_code} - {response.text}")
            except requests.exceptions.ConnectionError:
                st.error(" Fehler: Konnte keine Verbindung zum Backend herstellen. Läuft Uvicorn?")