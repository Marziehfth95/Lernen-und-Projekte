1. Das Grundproblem: Warum reicht ein LLM allein nicht?

Stellen wir ein LLM (wie GPT) so vor:

_ Es ist extrem gut trainiert
_ Aber: Es kennt nur Daten bis zu einem bestimmten Zeitpunkt
_ Und es hat keinen Zugriff auf deine eigenen Daten

Zwei zentrale Probleme:
 Problem 1: Veraltetes Wissen
Modell wurde z.b bis August trainiert
Du fragst etwas von September
 Modell weiß es nicht

 Aber: Es antwortet trotzdem, dann ist Halluzination

Problem 2: Kein Zugriff auf private Daten

Du hast z.b:

HR Richtlinien
interne Dokumente
Unternehmensdaten

 Das Modell kennt diese Daten nicht

 Lösungsidee (klassisch, aber schlecht):

 Fine-Tuning

Problem:

teuer
langsam
unpraktisch bei ständig neuen Daten

 2. Die Idee von RAG (Retrieval-Augmented Generation)

RAG = LLM + externe Wissensquelle

Das ist der entscheidende Shift:

Das Modell holt sich relevante Informationen von außen, bevor es antwortet.

 Intuition:

Stell dir vor:

LLM = ein sehr kluger Student
RAG = Student + Zugriff auf Notizen + Google + Firmenwiki

 Natürlich wird die Antwort besser, aktueller und spezifischer

 3. Die zwei wichtigsten Pipelines in RAG

1. Data Injection Pipeline (Vorbereitung der Daten)

Das passiert einmal vorab

Ablauf:
 Schritt 1: Daten sammeln
PDFs
HTML
Excel
Datenbanken
Texte
 Schritt 2: Parsing + Chunking

 Große Texte werden in kleine Teile (Chunks) zerlegt

Warum?

LLM kann nicht riesige Texte auf einmal effizient nutzen
Retrieval funktioniert besser mit kleinen Einheiten
 Schritt 3: Embeddings

 Text werden zu Zahlen (Vektoren)

Warum?

Maschinen vergleichen Zahlen besser als Text
ermöglicht Similarity Search
 Schritt 4: Speicherung in Vector Database

 Beispiele:

Pinecone
Weaviate
FAISS

Dort wird gespeichert:

jeder Text-Chunk als Vektor
 Ergebnis:

Wir haben jetzt eine durchsuchbare Wissensbasis

4. Retrieval Pipeline (zur Laufzeit)

Jetzt kommt der spannende Teil 

Ablauf bei einer User Anfrage:
 1. User stellt Frage

Beispiel:

"Wie ist die Urlaubsregelung in meiner Firma?"

 2. Frage wird auch embedded

 Frage auch zu Vektor

 3. Suche im Vector DB

 Ähnliche Inhalte werden gefunden

z.b.:

HR-Dokumente
Urlaubspolicies
 4. Kontext wird gebaut

 Die gefundenen Textstellen = Kontext

 5. Prompt + Kontext werden LLM gegeben

Jetzt bekommt das Modell:

die Frage
den Kontext
eine Instruktion

z.b.: "Beantworte die Frage nur basierend auf diesem Kontext"

 6. Antwort wird generiert

 Jetzt ist die Antwort:

genauer
weniger Halluzination
auf  Daten bezogen
 5. Warum reduziert RAG Halluzination?

Ohne RAG Modell rät

Mit RAG:  Modell liest zuerst relevante Infos

 Es antwortet also auf Basis echter Daten

 6. Warum ist RAG so wichtig in der Praxis?

Der Dozent sagt es auch:

 60–70 % der AI-Projekte nutzen RAG

Warum?

- Kein Fine-Tuning nötig
- Funktioniert mit aktuellen Daten
- Funktioniert mit privaten Daten
- Schnell aktualisierbar
- Kosteneffizient
7. Beispiel aus der Realität
🔎 Perplexity AI basiert stark auf RAG

Was passiert dort?

Frage -> Websuche (Retrieval)
Inhalte -> LLM (Augmentation)
Antwort -> generiert (Generation)


8. Die drei Kernbegriffe 

Retrieval: relevante Infos finden

Augmentation: Kontext dem Modell geben

Generation: Antwort erzeugen

9. Kurz zusammengefasst 

RAG ist ein Ansatz, bei dem ein LLM mit externem Wissen kombiniert wird
um bessere, aktuellere und spezifische Antworten zu erzeugen

Es besteht aus:

1. Data Injection Pipeline
Daten -> Chunking -> Embeddings -> Vector DB
2. Retrieval Pipeline
Query -> Embedding -> Suche -> Kontext -> LLM -> Antwort

