import pandas as pd
import numpy as np

def generate_billing_data():
    print("Olist Bestelldaten werden gelesen")
    try:
        orders = pd.read_csv('data/olist_orders_dataset.csv')
    except FileNotFoundError:
        print("FEHLER: olist_orders_dataset.csv nicht im 'data' Ordner gefunden!")
        return

    # Zeitstempel in Datumsobjekte umwandeln
    orders['order_purchase_timestamp'] = pd.to_datetime(orders['order_purchase_timestamp'])

    # Den exakten Zeitraum des Datensatzes ermitteln
    start_date = orders['order_purchase_timestamp'].min().floor('D')
    end_date = orders['order_purchase_timestamp'].max().ceil('D')
    
    print(f"Generiere Cloud-Kosten vom {start_date.date()} bis {end_date.date()}...")
    date_range = pd.date_range(start=start_date, end=end_date, freq='D')

    # Für reproduzierbare Ergebnisse
    np.random.seed(42) 
    billing_data = []

    for date in date_range:
        # Tägliche Basis Kosten für unsere Infrastruktur
        # Kubernetes Cluster Kosten
        eks_cost = np.random.uniform(150, 250) 
        # Datenbank Kosten
        rds_cost= np.random.uniform(80, 120)  
        
        # Anomalien einbauen (5% Wahrscheinlichkeit für einen massiven Kosten Spike)
        # z.B. weil Kubernetes wegen fehlerhaftem Code massiv hochskaliert ist
        if np.random.rand() > 0.95:
            eks_cost *= np.random.uniform(4, 7) 
            
        billing_data.append({'date': date.strftime('%Y-%m-%d'), 'service': 'AWS_EKS_Kubernetes', 'daily_cost_usd': round(eks_cost, 2)})
        billing_data.append({'date': date.strftime('%Y-%m-%d'), 'service': 'AWS_RDS_Database', 'daily_cost_usd': round(rds_cost, 2)})

    # Als CSV speichern
    df_billing = pd.DataFrame(billing_data)
    df_billing.to_csv('data/cloud_billing.csv', index=False)
    
    print(" ERFOLG: 'cloud_billing.csv' wurde im 'data' Ordner erstellt!")
    print(f"Es wurden {len(df_billing)} Abrechnungstage generiert.")

if __name__ == "__main__":
    generate_billing_data()