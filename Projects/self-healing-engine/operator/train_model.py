import pandas as pd
from sklearn.ensemble import IsolationForest
import joblib

# 1. Die historischen Daten laden
print("Daten aus prometheus_data.csv werden geladen")
df = pd.read_csv('prometheus_data.csv')

# 2. Features (Merkmale) definieren
# Für unser PoC nutzen wir die Anzahl der Neustarts als Hauptmerkmal
# In einem echten Cluster würde ich hier auch CPU Auslastung und RAM einfügen.
X = df[['restarts']]

# 3. Das Modell initialisieren
# 'contamination=0.05' bedeutet, dass wir schätzen, dass ca. 5% unserer Daten Anomalien sind
print("Trainieren Machine Learning Modell (Isolation Forest) ")
model = IsolationForest(contamination=0.05, random_state=42)

# 4. Das Modell mit unseren Daten trainieren
model.fit(X)

# 5. Das fertige Gehirn abspeichern
joblib.dump(model, 'model.pkl')
print("Modell erfolgreich trainiert und als 'model.pkl' gespeichert!")