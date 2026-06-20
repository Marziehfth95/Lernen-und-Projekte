# Transfer Learning for NLP with TensorFlow Hub

Dieses Projekt basiert auf dem Coursera Guided Project [Transfer Learning for NLP with TensorFlow Hub](https://www.coursera.org/projects/transfer-learning-nlp-tensorflow-hub). Die ursprüngliche Lernumgebung des Guided Projects wurde von mir in eine lokale, reproduzierbare und mit Docker ausführbare Entwicklungsumgebung überführt, damit das Projekt unabhängig von der Coursera Plattform in Cursor weiterentwickelt, nachvollzogen und eigenstaendig ausgebaut werden kann.

Ziel dieser Umsetzung war es, aus einem geführten Online Lab ein initiativer nutzbares Projekt zu machen: mit klarer Projektstruktur, wiederverwendbarer Container Konfiguration, JupyterLab-, TensorBoard- und CLI-Support sowie einer stabilen TensorFlow-Hub-Umgebung fuer eigenes Experimentieren und Weiterlernen.

## Ausfuehrung in Void ohne Browser

Falls die Dev Container Integration in Void oder andere Umgebungen wie Cursor, VS Code usw. nicht vollständig funktioniert, kann man das Projekt direkt in Void mit einem lokalen Projekt Kernel ausführen. Dafür wird eine lokale `.venv` im Projektordner erstellt, die dieselben Python Abhängigkeiten wie die Docker Umgebung verwendet.

```bash
make local-setup
```

Danach im Notebook als Interpreter oder Kernel wählen:

- `Coursera TF Hub (Local)`
- oder `.venv/bin/python`

Nicht verwenden:

- eine globale Homebrew-Python-Installation
- das alte `tfhub_env`
- andere projektfremde virtuelle Environments

## Was enthalten ist

- Ein isolierter Docker Workspace für TensorFlow, TensorFlow Hub, JupyterLab und TensorBoard
- Eine `.devcontainer`-Konfiguration für Cursor
- Ein kleines Python Projekt unter `src/tfhub_lab/`
- Eine Notebook Vorlage unter `notebooks/transfer_learning_nlp_tfhub.ipynb`
- Ein CLI Trainingseinstieg über `python -m tfhub_lab.train`

## Warum diese Paket-Versionen

Das alte lokale `tfhub_env` in dem Workspace scheitert an der aktuellen Keras-/TF-Hub-Kombination. Deshalb pinnt dieses Projekt die Umgebung bewusst auf:

- `tensorflow==2.16.2`
- `tensorflow-hub==0.16.1`
- `tf-keras==2.16.0`

So vermeidet man den bekannten `tf_keras`-Importfehler und hat eine stabile Basis für `hub.KerasLayer`.

## Projektstruktur

```text
coursera-transfer-learning-nlp-tfhub/
├── .devcontainer/
├── docker/
├── notebooks/
├── data/raw/
├── artifacts/
├── logs/
├── src/tfhub_lab/
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── requirements.txt
```
## Hinweis:
Die Einrichtung von Docker Container für dieses Projekt wurde mit Hilfe Codex gemacht. 

## Daten vorbereiten

Das Lab arbeitet mit dem Datensatz "Quora Insincere Questions". Lege die Datei `train.csv` hier ab:

```text
data/raw/train.csv
```

Erwartete Spalten:

- `question_text`
- `target`

## In Cursor öffnen

1. Öffne den Ordner `coursera-transfer-learning-nlp-tfhub` in Cursor.
2. Stelle sicher, dass Docker Desktop läuft.
3. Nutze in Cursor den Devcontainer Workflow:
   - `Reopen in Container`
4. Danach kann man direkt im Container mit allen installierten Abhängigkeiten arbeiten.

## Ohne Cursor starten

```bash
make build
make up
make shell
```

## Jupyter Lab starten

Container zuerst starten:

```bash
make up
```

Dann JupyterLab:

```bash
make notebook
```

Danach im Browser:

- `http://localhost:8888`

## TensorBoard starten

```bash
make tensorboard
```

Danach im Browser:

- `http://localhost:6006`

## Training ueber CLI

Mit dem Standardmodell:

```bash
make train
```

Oder direkt:

```bash
python -m tfhub_lab.train \
  --data-path /workspace/data/raw/train.csv \
  --model-name nnlm_50d \
  --epochs 3
```

Verfügbare Modelle:

- `swivel_20d`
- `nnlm_50d`
- `nnlm_128d`
- `nnlm_50d_finetune`

## Typischer Arbeitsablauf für das Lab

1. Datensatz nach `data/raw/train.csv` legen.
2. Container in Cursor/Void öffnen.
3. Notebook `notebooks/transfer_learning_nlp_tfhub.ipynb` starten.
4. Erst kleine Embedding-Modelle vergleichen.
5. Danach das feinjustierbare Modell `nnlm_50d_finetune` ausprobieren.
6. Trainingsmetriken mit TensorBoard vergleichen.

## Wichtige Pfade

- Datensatz: `data/raw/train.csv`
- Trainingsartefakte: `artifacts/`
- TensorBoard-Logs: `logs/`
- Python-Code: `src/tfhub_lab/`
