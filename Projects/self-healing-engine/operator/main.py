from flask import Flask, request, jsonify
from kubernetes import client, config
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 1. Verbindung zum Kubernetes-Cluster aufbauen
try:
    config.load_kube_config() # Nutzt deine lokale K8s-Konfiguration (genau wie 'kubectl')
    logging.info("Erfolgreich mit dem lokalen Kubernetes-Cluster verbunden.")
except Exception as e:
    logging.error(f"Konnte nicht mit Kubernetes verbinden: {e}")

# Die API-Schnittstelle, um Deployments zu steuern
v1_apps = client.AppsV1Api()

@app.route('/webhook', methods=['POST'])
def webhook():
    """Dieser Endpoint empfängt später die Alarme von Prometheus."""
    data = request.json
    logging.info("🚨 Alarm empfangen!")
    
    # Prüfen, ob Alarme im Payload sind
    if data and 'alerts' in data:
        for alert in data['alerts']:
            # Wir reagieren nur auf feuernde Alarme für unsere spezifische Regel
            if alert.get('status') == 'firing' and alert['labels'].get('alertname') == 'HighRestartRate':
                
                # Ziel-App extrahieren (aus dem Alert)
                namespace = alert['labels'].get('namespace', 'default')
                app_name = alert['labels'].get('container', 'victim-app')
                
                logging.info(f"🛠️  Starte Self-Healing für {app_name} im Namespace {namespace}...")
                heal_deployment(namespace, app_name)
                
    return jsonify({"status": "success"}), 200

def heal_deployment(namespace, name):
    """Führt den eigentlichen Rollback durch (ersetzt das Image wieder durch v1)."""
    try:
        # Wir sagen K8s: Überschreibe das Image dieses Deployments wieder mit v1
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": name, "image": f"{name}:v1"}]
                    }
                }
            }
        }
        v1_apps.patch_namespaced_deployment(name=name, namespace=namespace, body=patch)
        logging.info(f"✅ Self-Healing erfolgreich! {name} wurde auf Version v1 zurückgerollt.")
    except Exception as e:
        logging.error(f"❌ Fehler beim Self-Healing: {e}")

if __name__ == '__main__':
    # Engine läuft lokal auf Port 5000
    app.run(host='0.0.0.0', port=5000)