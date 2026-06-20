import requests
import pandas as pd
import time

# Die URL des lokalen Prometheus Tunnels
PROMETHEUS_URL = "http://localhost:9090/api/v1/query_range"

# Die Metrik, die wir untersuchen wollen (Neustarts der victim-app)
query = 'kube_pod_container_status_restarts_total{namespace="default", container="victim-app"}'

# Wir holen die Daten der letzten 3 Stunden
end_time = int(time.time())
start_time = end_time - (3 * 60 * 60) 

# Parameter für die Prometheus-API
params = {
    'query': query,
    'start': start_time,
    'end': end_time,
    'step': '15s' # Alle 15 Sekunden einen Datenpunkt erfassen
}

print("Sammle historische Daten von Prometheus...")
response = requests.get(PROMETHEUS_URL, params=params)

if response.status_code == 200:
    data = response.json()['data']['result']
    if not data:
        print("Keine Daten gefunden! Laufen die Pods der victim-app?")
    else:
        records = []
        # Prometheus liefert die Daten verschachtelt zurück, wir entpacken sie:
        for result in data:
            pod_name = result['metric'].get('pod', 'unknown-pod')
            for value in result['values']:
                # value[0] ist der UNIX-Zeitstempel, value[1] ist der Wert der Metrik
                records.append({
                    "timestamp": value[0], 
                    "pod": pod_name,
                    "restarts": int(value[1])
                })

        # Daten in ein Pandas DataFrame (Tabelle) laden
        df = pd.DataFrame(records)
        # Den UNIX-Zeitstempel in ein lesbares Datum umwandeln
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
        
        # Als CSV-Datei speichern
        df.to_csv('prometheus_data.csv', index=False)
        print(f" Erfolg! {len(df)} Datenpunkte extrahiert und in 'prometheus_data.csv' gespeichert.")
else:
    print(f" Fehler bei der Verbindung zu Prometheus: HTTP {response.status_code}")