import requests
import pandas as pd
from datetime import datetime

def fetch_live_kubecost():
    print("Lade echte Echtzeit-Kosten aus dem Kubernetes-Cluster...")
    # Kubecost Allocation API (Interne Kubernetes DNS)
    # Format: http://<service-name>.<namespace>.svc.cluster.local:<port>
    url = "http://kubecost-cost-analyzer.kubecost.svc.cluster.local:9090/model/allocation?window=1h&aggregate=namespace"

    response = requests.get(url)
    data = response.json()

    billing_records = []
    # Parse das JSON von Kubecost
    for window in data['data']:
        for namespace, metrics in window.items():
            if namespace != '__idle__':
                billing_records.append({
                    'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
                    'service': f"K8s_Namespace_{namespace}",
                    'daily_cost_usd': round(metrics['totalCost'], 4)
                })

    df = pd.DataFrame(billing_records)
    df.to_csv('data/cloud_billing.csv', index=False)
    print(f"✅ Live-Daten gespeichert! ({len(df)} Einträge)")

if __name__ == "__main__":
    fetch_live_kubecost()