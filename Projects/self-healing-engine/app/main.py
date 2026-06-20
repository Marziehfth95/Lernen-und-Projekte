from flask import Flask, jsonify

app = Flask(__name__)

#globaler Status,um zu simulieren, ob App gesund ist
is_healthy = True

@app.route('/')
def index():
    return "Willkommen bei der Self-Healing Test App!", 200

@app.route('/health')
def health():
    """Dieser Endpoint wird von Kubernetes abgefragt (Liveness Probe)."""
    global is_healthy
    if is_healthy:
        return jsonify({"status": "ok", "message": "App läuft perfekt"}), 200
    else:
        # Gibt einen 500 Internal Server Error zurück, wenn die App 'kaputt' ist
        return jsonify({"status": "error", "message": "App ist abgestürzt!"}), 500

@app.route('/crash', methods=['POST'])
def crash():
    """Unser Self Destruct Button."""
    global is_healthy
    is_healthy = False
    return jsonify({"message": "App wurde absichtlich zerstört. /health liefert nun 500er Fehler."}), 200

if __name__ == '__main__':
    # Läuft auf Port 8080 und lauscht auf allen Interfaces (0.0.0.0 ist wichtig für Docker!)
    app.run(host='0.0.0.0', port=8080)