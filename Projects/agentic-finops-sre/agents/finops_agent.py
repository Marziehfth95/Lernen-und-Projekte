import os
import io
import contextlib
import traceback
from typing import TypedDict
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

# Lade Umgebungsvariablen (OPENAI_API_KEY)
load_dotenv()

# 1. DAS GEDÄCHTNIS (State)
# Hier definieren wir, was sich unsere Agenten über den Workflow hinweg merken müssen.
class AgentState(TypedDict):
    query: str          # Die Aufgabe des Nutzers
    code: str           # Der generierte Python-Code
    error: str          # Fehlermeldungen (falls der Code abstürzt)
    result: str         # Erfolgreiche Text-Ausgabe des Codes
    insights: str       # Die finale Business-Zusammenfassung
    iterations: int     # Zähler, um Endlosschleifen zu verhindern

llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
# ---------------------------------------------------------
# 2. DIE AGENTEN (Nodes)
# ---------------------------------------------------------

def data_analyst_agent(state: AgentState):
    """Schreibt oder korrigiert den Python-Code."""
    print(f"\n🧠 [Data Analyst] Überlege... (Iteration {state.get('iterations', 0) + 1})")
    
    query = state["query"]
    error = state.get("error", "")
    
    # Wenn wir einen Fehler haben, sagen wir der KI, sie soll ihn reparieren
    if error:
        prompt_text = f"""Du bist ein Senior FinOps Data Engineer. 
        Dein letzter Python-Code ist fehlgeschlagen mit diesem Fehler:
        {error}
        
        Bitte schreibe den Code neu und korrigiere den Fehler. 
        Gib NUR validen Python-Code zurück, keine Markdown-Blöcke (kein ```python), keinen Erklärtext.
        """
    else:
        prompt_text = f"""Du bist ein Senior FinOps Data Engineer.
        Du hast echte CSV-Dateien im Ordner 'data/':
        - 'data/olist_orders_dataset.csv' (Spalten u.a.: order_id, order_purchase_timestamp)
        - 'data/olist_order_payments_dataset.csv' (Spalten u.a.: order_id, payment_value)
        - 'data/cloud_billing.csv' (Spalten: date, service, daily_cost_usd)
        
        Aufgabe: {query}
        
        WICHTIGE REGELN FÜR DEINEN CODE:
        1. Lade zwingend die echten CSV-Dateien mit Pandas. Erzeuge NIEMALS Dummy-Daten!
        2. Nutze pd.to_datetime() für die Datumsspalten. Extrahiere das Datum (YYYY-MM-DD) aus 'order_purchase_timestamp', damit du es sauber mit der Spalte 'date' aus cloud_billing.csv mergen kannst.
        3. Fasse zuerst die Bestellungen und Zahlungen zusammen, aggregiere sie pro Tag und merge sie dann mit den Cloud-Kosten pro Tag.
        4. Gib die finalen Geschäftszahlen am Ende per print() aus (Umsatz, Kosten, Verhältnis).
        5. Erstelle ein Matplotlib-Diagramm und speichere es als 'finops_report.png'.
        Gib NUR den nackten, ausführbaren Python-Code zurück.
        """
    
    # LLM aufrufen
    response = llm.invoke(prompt_text)
    code = response.content.strip()
    
    # Manchmal gibt das LLM trotzdem Markdown zurück, wir bereinigen das sicherheitshalber
    if code.startswith("```python"):
        code = code[9:-3].strip()
        
    return {"code": code, "iterations": state.get("iterations", 0) + 1}


def executor_agent(state: AgentState):
    """Führt den Code aus und fängt Fehler ab (Self-Healing-Trigger)."""
    print("⚙️ [Executor] Führe Code aus...")
    code = state["code"]
    
    # Wir leiten die print()-Ausgaben des Codes in eine Variable um
    output_buffer = io.StringIO()
    
    try:
        with contextlib.redirect_stdout(output_buffer):
            # VORSICHT: exec() führt Code aus. In Produktion gehört das in einen sicheren Docker-Container!
            exec(code, globals()) 
        
        result = output_buffer.getvalue()
        print("✅ [Executor] Code erfolgreich ausgeführt!")
        return {"result": result, "error": ""} # Kein Fehler
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        print(f"❌ [Executor] Fehler aufgetreten: {type(e).__name__}")
        return {"error": error_msg, "result": ""}


def reviewer_agent(state: AgentState):
    """Fasst die Ergebnisse für das Management zusammen."""
    print("📝 [Reviewer] Schreibe Business-Zusammenfassung...")
    
    result = state["result"]
    prompt = f"""Du bist ein Cloud FinOps Manager. 
    Hier sind die Rohdaten-Ausgaben aus unserer Analyse:
    {result}
    
    Schreibe eine kurze, professionelle Management-Zusammenfassung (max 3 Sätze) 
    über die Cloud-Kosten und die Profitabilität.
    """
    response = llm.invoke(prompt)
    return {"insights": response.content}


# ---------------------------------------------------------
# 3. ROUTING-LOGIK (Entscheidungen)
# ---------------------------------------------------------

def should_continue(state: AgentState):
    """Entscheidet, ob der Code zurück zum Analysten muss oder zum Reviewer kann."""
    if state.get("error"):
        if state.get("iterations", 0) >= 4:
            print("🛑 [System] Abbruch: Zu viele Fehlversuche.")
            return END
        return "analyst" # Zurück zum Analysten (Loop)
    return "reviewer" # Wenn kein Fehler, weiter zum Reviewer


# ---------------------------------------------------------
# 4. DEN GRAPH BAUEN
# ---------------------------------------------------------
workflow = StateGraph(AgentState)

# Nodes hinzufügen
workflow.add_node("analyst", data_analyst_agent)
workflow.add_node("executor", executor_agent)
workflow.add_node("reviewer", reviewer_agent)

# Kanten (Flow) definieren
workflow.set_entry_point("analyst")
workflow.add_edge("analyst", "executor")
workflow.add_conditional_edges("executor", should_continue)
workflow.add_edge("reviewer", END)

# Die fertige Agenten-App kompilieren
finops_ai_app = workflow.compile()


# ==========================================
# TEST-BEREICH (Wird ausgeführt, wenn du das Skript startest)
# ==========================================
if __name__ == "__main__":
    task = ("Finde heraus, an welchem Tag im Jahr 2018 wir den meisten Umsatz gemacht haben "
            "und wie hoch unsere Cloud-Kosten an genau diesem Tag waren. "
            "Berechne das Verhältnis von Cloud-Kosten zu Umsatz in Prozent für diesen Tag.")
    
    print(f"Start-Aufgabe: {task}")
    
    # Starte den Graph
    final_state = finops_ai_app.invoke({"query": task, "iterations": 0})
    
    print("\n" + "="*50)
    print("📊 FINALE MANAGEMENT ZUSAMMENFASSUNG:")
    print("="*50)
    print(final_state.get("insights", "Keine Zusammenfassung erstellt."))
    print("="*50)
    print("Hat die KI ein Bild erstellt? Schau nach 'finops_report.png' im Ordner!")