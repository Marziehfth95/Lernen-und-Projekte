#!/usr/bin/env bash
# Interactive LOCAL demo: real FastAPI backend + browser UI on http://127.0.0.1:${PORT:-8765}/app/
# Fresh disposable SQLite per launch (deleted on exit), synthetic identities, deterministic
# mock provider by default (DETERMINISTIC_MOCK_NOT_REAL_AI), no Azure, no paid model, no
# external send. "Real Local AI (Ollama)" is selectable in the UI and uses only the loopback
# Ollama endpoint (REAL_MODEL_OUTPUT); it never pulls a model and never falls back to the mock.
#
#   scripts/run_interactive_demo.sh [--port 8765] [--ollama-model qwen3.5:9b]
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
export APP_ENV=local ALLOW_PAID_MODEL_CALLS=false
unset FOUNDRY_PROJECT_ENDPOINT AZURE_CLIENT_ID APPLICATIONINSIGHTS_CONNECTION_STRING || true
cd "$REPO"
exec "$PYTHON" -I -m process_optimizer.demo.interactive_launcher "$@"
