import os
import hmac
import hashlib
import logging
import requests
from fastapi import FastAPI, Request, HTTPException, Header
from dotenv import load_dotenv
from github import Github, GithubIntegration
from fastapi import BackgroundTasks
import chromadb
import anthropic

# 1. Konfiguration
load_dotenv()
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
GITHUB_APP_ID = int(os.getenv("GITHUB_APP_ID"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Neue Umgebungsvariable für den nächtlichen Job
CRON_SECRET = os.getenv("CRON_SECRET", "super-geheimes-lokales-passwort")

# 2. Initialisierung & Gedächtnis (Memory)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI(title="AI Code Review Bot mit Memory")

# Initialisiere ChromaDB (Speichert Daten lokal im Ordner 'chroma_db')
chroma_client = chromadb.PersistentClient(path="./chroma_db")
memory_collection = chroma_client.get_or_create_collection(name="pr_reviews")

def get_github_client(installation_id):
    """Authentifiziert sich als GitHub App Installation via .pem Datei"""
    try:
        with open("private-key.pem", "r") as f:
            private_key = f.read().strip()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Security Error: private-key.pem fehlt!")

    auth = GithubIntegration(GITHUB_APP_ID, private_key)
    token = auth.get_access_token(installation_id).token
    return Github(token), token

def analyze_code_with_claude(diff: str) -> dict:
    """Sendet den Diff an Claude und fordert das Auto-Fix JSON-Format an"""
    
    # Prompt Engineering: Strikte Anweisungen für den Auto-Fix
    system_prompt = """Du bist ein Senior Code Reviewer und Auto-Fix-Bot. 
    Analysiere den folgenden Git-Diff auf Bugs, Sicherheitslücken (z.B. SQL-Injection) und schlechte Performance.
    
    Du MUSST ausschließlich ein valides JSON-Objekt zurückgeben. Nutze absolut keine Markdown-Blöcke (wie ```json) und keinen Text vor oder nach dem JSON.
    
    Das JSON muss exakt dieses Format haben:
    {
      "issues": [
        {
          "file": "dateiname.py",
          "line": 10,
          "description": "Kurze Beschreibung des Fehlers",
          "original_code": "exakte Zeile(n) des falschen Codes aus dem Diff",
          "suggested_fix": "die korrigierte Version des Codes"
        }
      ]
    }
    
    WICHTIGE REGEL: 
    Der Wert für "original_code" MUSS exakt (Zeichen für Zeichen, inklusive Leerzeichen) mit dem fehlerhaften Code im Diff übereinstimmen. Dein "original_code" wird in einem Python .replace() Befehl genutzt. Wenn er nicht exakt matcht, schlägt das Auto-Fixing fehl!
    Wenn der Code keine Fehler enthält, gib {"issues": []} zurück.
    """
    
    try:
        response = claude_client.messages.create(
            model="claude-3-5-sonnet-20241022", # Das beste Modell für Code
            max_tokens=2000,
            temperature=0.1, # Niedrige Temperatur für deterministischen, exakten Code
            system=system_prompt,
            messages=[{"role": "user", "content": f"Hier ist der Git-Diff:\n{diff}"}]
        )
        
        # Antworttext holen und mögliche Markdown-Reste bereinigen, falls Claude sich nicht an die Regeln hält
        response_text = response.content[0].text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:-3].strip()
        elif response_text.startswith("```"):
            response_text = response_text[3:-3].strip()
            
        return json.loads(response_text)
        
    except json.JSONDecodeError as e:
        logger.error(f"Claude hat kein valides JSON zurückgegeben: {e}")
        return {"issues": []}
    except Exception as e:
        logger.error(f"Anthropic API Fehler: {e}")
        return {"issues": []}



def check_memory_for_similar_issues(issue_description: str):
    """Sucht in der Datenbank nach ähnlichen Fehlern aus der Vergangenheit."""
    # Falls die Datenbank noch komplett leer ist, suchen wir gar nicht erst
    if memory_collection.count() == 0:
        return None
        
    results = memory_collection.query(
        query_texts=[issue_description],
        n_results=1
    )
    
    if results['documents'] and len(results['documents'][0]) > 0:
        metadata = results['metadatas'][0][0] if results['metadatas'] else {"pr": "unbekannt"}
        past_pr = metadata.get("pr", "unbekannt")
        return f"🧠 *Memory-Check: Dieser Fehler wurde bereits in PR #{past_pr} gemacht. Bitte achte in Zukunft darauf!*"
    
    return None

def save_issue_to_memory(issue_description: str, pr_number: int):
    """Speichert den gefundenen Fehler für die Zukunft im Gedächtnis ab."""
    issue_id = f"pr_{pr_number}_{hashlib.md5(issue_description.encode()).hexdigest()[:8]}"
    
    memory_collection.add(
        documents=[issue_description],
        metadatas=[{"pr": pr_number}],
        ids=[issue_id]
    )
    logger.info(f"Fehler aus PR #{pr_number} ins Langzeitgedächtnis gespeichert.")

@app.post("/webhook")
async def github_webhook(request: Request, x_hub_signature_256: str = Header(None)):
    payload_body = await request.body()
    
    hash_object = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), payload_body, hashlib.sha256)
    if not hmac.compare_digest("sha256=" + hash_object.hexdigest(), x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    
    if "pull_request" in payload and payload.get("action") in ["opened", "synchronize"]:
        installation_id = payload["installation"]["id"]
        pr_number = payload["pull_request"]["number"]
        repo_name = payload["repository"]["full_name"]
        
        logger.info(f"Starte AI Review für PR #{pr_number} im Repo {repo_name}...")
        
        g, token = get_github_client(installation_id)
        repo = g.get_repo(repo_name)
        pull_request = repo.get_pull(pr_number)
        
        diff_response = requests.get(pull_request.diff_url, headers={"Authorization": f"token {token}"})
        diff = diff_response.text
        
        # 1. Code analysieren
        review_results = analyze_code_with_claude(diff[:8000])
        print(f"DEBUG: Gefundene Fehler: {review_results}")
        
        comment_body = "🤖 **AI Code Review:**\n\n"
        
        # 2. Gedächtnis abfragen & Ergebnisse aufbauen
        for issue in review_results.get("issues", []):
            # Branch-Namen aus dem Payload holen (brauchen wir für den Commit)
            branch_name = payload["pull_request"]["head"]["ref"]
        
        # 2. Gedächtnis abfragen & Auto-Fix anwenden
        for issue in review_results.get("issues", []):
            desc = issue['description']
            memory_warning = check_memory_for_similar_issues(desc)
            
            comment_body += f"- **File**: `{issue['file']}` (Line {issue['line']})\n"
            comment_body += f"  - **Issue**: {desc}\n"
            
            # === NEU: AUTO-COMMIT LOGIK ===
            try:
                # Hole die aktuelle Datei von GitHub
                file_obj = repo.get_contents(issue['file'], ref=branch_name)
                file_content = file_obj.decoded_content.decode("utf-8")
                
                # Wenn der fehlerhafte Code gefunden wird, ersetze ihn
                if issue.get('original_code') in file_content:
                    new_content = file_content.replace(issue['original_code'], issue['suggested_fix'])
                    
                    # Pushe die Änderung als neuen Commit direkt in den PR-Branch!
                    repo.update_file(
                        file_obj.path,
                        message=f"🤖 AI Auto-Fix: {desc}",
                        content=new_content,
                        sha=file_obj.sha,
                        branch=branch_name
                    )
                    comment_body += f"  - ✅ **Auto-Fix angewendet:** Code wurde korrigiert und gepusht!\n"
                else:
                    comment_body += f"  - ⚠️ **Fix-Vorschlag:** `{issue['suggested_fix']}` (Konnte nicht automatisch angewendet werden)\n"
            except Exception as e:
                logger.error(f"Auto-Fix fehlgeschlagen für {issue['file']}: {e}")
                comment_body += f"  - 💡 **Fix-Vorschlag:** `{issue['suggested_fix']}`\n"
            # ==============================

            if memory_warning:
                comment_body += f"  - {memory_warning}\n"
                
            comment_body += "\n"
            save_issue_to_memory(desc, pr_number)
        
    return {"status": "success"}

def consolidate_memory():
    """Das ist der eigentliche 'Traum'. Hier passiert die schwere Offline-Arbeit."""
    logger.info("💤 Bot beginnt zu träumen (Memory Consolidation)...")
    
    try:
        # 1. Alle aktuellen Erinnerungen aus der Vektordatenbank holen
        all_memories = memory_collection.get()
        documents = all_memories.get("documents", [])
        ids = all_memories.get("ids", [])
        
        # Wenn wir weniger als 3 Fehler haben, lohnt sich das Zusammenfassen noch nicht
        if not documents or len(documents) < 3:
            logger.info("Zu wenige neue Erinnerungen. Der Bot schläft traumlos weiter.")
            return

        logger.info(f"Analysiere {len(documents)} Erinnerungen...")

        # 2. Claude bitten, die Fehler zu "Meta-Wissen" zusammenzufassen
        system_prompt = """Du bist das Unterbewusstsein eines Senior Code Reviewers. 
        Deine Aufgabe ist "Memory Consolidation" (Wissenskomprimierung).
        Lies die Liste der vergangenen Code-Fehler und erkenne Muster. Fasse ähnliche Fehler zu übergeordneten "Meta-Regeln" zusammen.
        
        Antworte AUSSCHLIESSLICH mit einem validen JSON in diesem Format:
        {
            "meta_rules": [
                "Generelle Regel 1 basierend auf den Fehlern",
                "Generelle Regel 2..."
            ]
        }
        """
        
        user_prompt = f"Hier sind die Fehler der letzten Zeit:\n{json.dumps(documents, indent=2)}"

        response = claude_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1500,
            temperature=0.3, # Etwas Kreativität erlaubt, um Muster zu erkennen
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        # JSON bereinigen und parsen
        response_text = response.content[0].text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:-3].strip()
        elif response_text.startswith("```"):
            response_text = response_text[3:-3].strip()
            
        meta_knowledge = json.loads(response_text)
        new_rules = meta_knowledge.get("meta_rules", [])
        
        if new_rules:
            # 3. Alte, unwichtige Duplikate aus der Datenbank löschen
            memory_collection.delete(ids=ids)
            
            # 4. Das neue, kompakte Meta-Wissen abspeichern
            new_ids = [f"meta_{hashlib.md5(rule.encode()).hexdigest()[:8]}" for rule in new_rules]
            
            # Wir markieren diese neuen Einträge mit dem Metadatum "type": "meta_rule"
            memory_collection.add(
                documents=new_rules,
                metadatas=[{"type": "meta_rule", "pr": "System-Dream"}] * len(new_rules),
                ids=new_ids
            )
            logger.info(f"✨ Traumphase beendet! {len(ids)} alte Fehler gelöscht und zu {len(new_rules)} kompakten Meta-Regeln komprimiert.")
        else:
            logger.info("Keine Muster erkannt. Gedächtnis bleibt unverändert.")

    except json.JSONDecodeError as e:
        logger.error(f"Traum war unklar (JSON Fehler von Claude): {e}")
    except Exception as e:
        logger.error(f"Ein Albtraum ist aufgetreten (Systemfehler): {e}")
@app.post("/system/dream")
async def trigger_dream_sequence(
    background_tasks: BackgroundTasks, 
    authorization: str = Header(None)
):
    """Dieser Endpunkt wird jede Nacht von GitHub Actions aufgerufen."""
    
    # 1. Sicherheits-Check: Ist das wirklich unser Cron-Job?
    expected_token = f"Bearer {CRON_SECRET}"
    if authorization != expected_token:
        raise HTTPException(status_code=401, detail="Zugriff verweigert. Nur für Träumer.")
    
    # 2. Den Traum im Hintergrund starten, damit GitHub nicht warten muss
    background_tasks.add_task(consolidate_memory)
    
    return {"status": "Träume süß! Offline-Prozess gestartet."}