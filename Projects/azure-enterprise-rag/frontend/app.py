import os

import streamlit as st
import requests

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Enterprise RAG", page_icon="🤖", layout="centered")

st.title("🤖 Enterprise RAG Assistant")
st.markdown("Lade ein Dokument hoch und stelle Fragen dazu. Die KI nutzt `gpt-5.6-luna` und sucht in PostgreSQL (`pgvector`).")

# Session State für die Chat-Historie initiieren
if "messages" not in st.session_state:
    st.session_state.messages = []

# --- SIDEBAR: DATEI UPLOAD ---
with st.sidebar:
    st.header("🔐 Anmeldung")
    access_token = st.text_input(
        "Entra Access Token",
        value=os.getenv("API_BEARER_TOKEN", ""),
        type="password",
        help="Token für die API-Audience; wird nicht in der Chat-Historie gespeichert.",
    )
    st.header("📄 Dokument-Upload")
    uploaded_file = st.file_uploader("PDF oder TXT hochladen", type=["pdf", "txt"])
    
    if st.button("Hochladen & Vektorisieren") and uploaded_file:
        if not access_token:
            st.error("Bitte zuerst ein Entra Access Token angeben.")
            st.stop()
        with st.spinner("Dokument wird verarbeitet..."):
            # Datei an das FastAPI Backend schicken
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
            response = requests.post(
                f"{API_URL}/upload",
                files=files,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=120,
            )
            
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
    if not access_token:
        st.error("Bitte zuerst ein Entra Access Token angeben.")
        st.stop()
    # 1. Frage des Users anzeigen und speichern
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. Antwort vom Backend holen
    with st.chat_message("assistant"):
        with st.spinner("GPT-5.6 analysiert deine Vektoren..."):
            try:
                response = requests.post(
                    f"{API_URL}/ask",
                    json={"question": prompt},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=120,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    answer = data.get("answer", "Keine Antwort erhalten.")
                    sources = data.get("sources", [])
                    
                    # Antwort anzeigen
                    st.markdown(answer)
                    
                    with st.expander("🔍 Autorisierte Quellen"):
                        if sources:
                            for source in sources:
                                st.markdown(
                                    f"- **[{source['citation']}]** {source['filename']} "
                                    f"(Dokument {source['document_id']}, Abschnitt {source['chunk_index']})"
                                )
                        else:
                            st.info("Die Antwort enthielt keine gültige Quellenreferenz.")
                        
                    # Antwort in die Historie speichern
                    st.session_state.messages.append({"role": "assistant", "content": answer})
                else:
                    st.error(f"API Fehler: {response.status_code} - {response.text}")
            except requests.exceptions.RequestException as exc:
                st.error(f"API-Verbindung fehlgeschlagen: {exc}")
