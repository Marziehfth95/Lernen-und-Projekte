# Jede Execution, die wir haben, muss hier geloggt werden, damit wir sie verfolgen können

import logging
import os
from datetime import datetime

# 1. Dateinamen definieren
LOG_FILE = f"{datetime.now().strftime('%m_%d_%Y_%H_%M_%S')}.log"

# 2. Nur den Pfad für das Ordner definieren
logs_dir = os.path.join(os.getcwd(), "logs")

# 3. Den Ordner erstellen (falls er nicht existiert)
os.makedirs(logs_dir, exist_ok=True)

# 4. Den finalen Pfad zur Datei zusammensetzen
LOG_FILE_PATH = os.path.join(logs_dir, LOG_FILE)

logging.basicConfig(
    filename=LOG_FILE_PATH,
    format="[ %(asctime)s ] %(lineno)d %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

if __name__ == "__main__":
    logging.info("Logging has started")