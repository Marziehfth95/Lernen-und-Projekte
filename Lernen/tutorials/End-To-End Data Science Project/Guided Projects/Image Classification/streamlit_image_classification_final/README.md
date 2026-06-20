## AI Image Classification Dashboard with Streamlit
Ein interaktives Web Dashboard zum Klassifizieren von Bildern in Echtzeit mithilfe moderner Deep Learning Modelle.
Das Projekt zeigt, wie man mit Streamlit, Hugging Face Transformers und einfacher Python Logik eine professionelle AI Applikation baut.

Dieses Projekt wurde aus dem Bereich Artificial Intelligence auf der Codecademy Seite ausgewählt:



#### Was dieses Projekt macht
Nutzer laden ein Bild hoch (oder testen mit Beispielbildern).

Das Dashboard klassifiziert das Bild mit zwei state of the art Modellen:

Google ViT‑Base‑Patch16‑224 (Vision Transformer)

Microsoft ResNet‑50 (Residual Network)

Es werden:
Top‑k Vorhersagen,Konfidenzen (Prozentwerte),Modell Vergleiche und Analytics dargestellt.

Verwendete Modelle
Modell	Architektur	Training / Anwendung
google/vit-base-patch16-224	Vision Transformer (ViT)	1.000 ImageNetKategorien, hohe Genauigkeit über viele Klassen.
microsoft/resnet-50	Residual Network (ResNet‑50)	1.000 ImageNet‑Kategorien, robuste klassische CNN‑Architektur.


#### Technologien & Tools
Python: Programmiersprache des Projekts

Streamlit: Für die interaktive Web‑UI

Transformers (Hugging Face):  Laden und Nutzen der vortrainierten Modelle

Pillow / PIL: Bildvorverarbeitung

Matplotlib : Visualisierung von Confusion Scores, Top‑k Vorhersagen und Analytics

Pandas: Speicherung und Analyse der Klassifikationshistorie

#### Hauptfunktionen des Dashboards
- Single Image Analysis

- Bild hochladen und anzeigen.

- Klassifizierung mit beidem Modellen

- Top‑k Vorhersagen (z.B. Top‑5) mit Prozent‑Konfidenzen.

- Classification History

- Protokollierung aller getätigten Klassifikationen.

- Anzeige von Zeitstempel, Modell, Bildname, Top‑Kategorie und Konfidenz.

- Analytics & Performance

Vergleich von:

- durchschnittlicher Konfidenz pro Modell,

- häufigsten Kategorien,

- Verarbeitungszeit.

- Visualisierungen (z.B. Balkendiagramme, Histogramme).

- Export & Portabilität

- Möglichkeit, die Klassifikationshistorie als CSV oder JSON zu exportieren.

Einsatz als Prototyp für produktive AI‑Tools.

#### Testdatensätze (optional zum Training / Testen)
Dieses Projekt kann mit verschiedenen Bild‑Datensätzen getestet werden, z.b.:

- Animal Image Dataset (90 Tiere)

- Cats vs Dogs Dataset

- CIFAR‑10

- ImageNet Sample Images

Ziel des Projekts
- Lernen, wie man AI Modelle in eine Web Anwendung einbettet.
- Praktische Erfahrung mit Streamlit Layouts, Session State und Modell Integration sammeln.
- Verstehen, wie Vision Transformers vs. klassische CNNs performen.
- Ein Projekt für Data Science / ML  aufbauen.

#### Lernziele
Mit diesem Projekt lerne ich:
- Wie man ein vortrainiertes Modell von Hugging Face nutzt.
- Echtzeit Bildklassifikation mit Streamlit umzusetzen.
- Klassifikationshistorie persistent zu speichern und zu analysieren.
- Performance Metriken und Visualisierungen zu erstellen


#### Hinweis zur Quelle

Dieses Projekt wurde inspiriert durch die Codecademy Serie „Build an AI Image Classification Dashboard with Streamlit“ im Bereich Artificial Intelligence:

Projects in AI – Codecademy
https://www.codecademy.com/projects/subject/artificial-intelligence

Durch die Umsetzung dieses Labs könnte ich echte Skills für AI Deployment üben, Dashboard Design und Data Science Workflows in der Praxis.
