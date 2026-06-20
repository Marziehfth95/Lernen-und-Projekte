from kubernetes import client, config
import requests
import pandas as pd
import joblib
import time
import logging

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# 1. Kubernetes Verbindung herstellen
try:
    config.load_kube_config()
    v1_apps = client.AppsV1Api()
    logging.info("Erfolgreich mit dem lokalen Kubernetes Cluster verbunden ")
except Exception as e:
    logging.error(f" K8s Fehler: {e}")

# 2. Das trainierte KI Modell laden
try:
    model= joblib.load('model.pkl')
    logging.info(" KI-Modell (Isolation Forest) erfolgreich geladen.")
except Exception as e:
    logging.error(f" Fehler beim Laden des Modells: {e}")

# Prometheus Setup
PROMETHEUS_URL = "http://localhost:9090/api/v1/query"
QUERY = 'kube_pod_container_status_restarts_total{namespace="default", container="victim-app"}'

def heal_deployment():
    """Führt präventiv den Rollback auf v1 durch."""
    try:
        patch = {"spec": {"template": {"spec": {"containers": [{"name": "victim-app", "image": "victim-app:v1"}]}}}}
        v1_apps.patch_namespaced_deployment(name="victim-app", namespace="default", body=patch)
        logging.info("🛠️ Self-Healing erfolgreich! Deployment wurde auf v1 zurückgesetzt.")
    except Exception as e:
        logging.error(f"Fehler beim Self Healing: {e}")

def monitor_cluster():
    """Die Endlosschleife, die kontinuierlich Metriken prüft und das Modell fragt."""
    logging.info("👁️ Starte proaktive KI-Überwachung... (Prüfung alle 10 Sekunden)")
    
    while True:
        try:
            # Live Daten von Prometheus holen
            response = requests.get(PROMETHEUS_URL, params={'query': QUERY})
            data = response.json().get('data', {}).get('result', [])
            
            if data:
                # Wir nehmen die Neustarts des ersten gefundenen Pods
                restarts = int(data[0]['value'][1])
                
                # Daten für das Modell aufbereiten (als Pandas DataFrame)
                df_live = pd.DataFrame([{'restarts': restarts}])
                
                # KI-Vorhersage: 1 bedeutet "Normal", -1 bedeutet "Anomalie"
                prediction = model.predict(df_live)[0]
                
                if prediction == -1:
                    logging.warning(f" ANOMALIE ERKANNT! Ungewöhnliches Verhalten ({restarts} Neustarts). Greife proaktiv ein!")
                    heal_deployment()
                    # Nach der Heilung 60 Sekunden pausieren, damit K8s Zeit hat, die neuen Pods zu starten
                    logging.info("⏳ Pausiere Überwachung für 60 Sekunden...")
                    time.sleep(60) 
                else:
                    logging.info(f"Status Normal: {restarts} Neustarts. Keine Anomalie erkannt.")
            else:
                logging.warning("Keine Metriken in Prometheus gefunden. Laufen die Pods?")
                
        except Exception as e:
            logging.error(f"Fehler bei der Überwachung: {e}")
        
        # 10 Sekunden warten bis zur nächsten Überprüfung
        time.sleep(10)

if __name__ == '__main__':
    # Startet die Endlosschleife
    monitor_cluster()