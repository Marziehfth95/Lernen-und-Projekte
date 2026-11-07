#!/usr/bin/env bash
# Local end-to-end demo on SQLite, PostgreSQL or both. Disposable stores only; no Azure,
# no model call, no external send. See docs/local_e2e_demo.md.
#
#   scripts/run_local_e2e_demo.sh [sqlite|postgres|both] [driver options]   (default: both)
#
# Driver options (for example --analysis-dir, --expected-evidence-sha256, --expected-digest)
# are passed to scripts/local_e2e_demo.py for every backend. With PO_DEMO_REPORT_DIR set, the
# JSON report of each backend is written to $PO_DEMO_REPORT_DIR/report-<backend>.json.
#
# PostgreSQL needs initdb and pg_ctl on PATH. A throw-away cluster is created in a temporary
# directory, listens on 127.0.0.1 only (PO_DEMO_PG_PORT, default 55433) and is stopped and
# deleted on exit, including after a failure.
set -euo pipefail

MODE="${1:-both}"
case "$MODE" in sqlite|postgres|both) ;; *) echo "usage: $0 [sqlite|postgres|both] [driver options]" >&2; exit 2;; esac
shift || true

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
PORT="${PO_DEMO_PG_PORT:-55433}"
DRIVER="$REPO/scripts/local_e2e_demo.py"
STATUS=0

# Fills REPORT=(--report <file>) when PO_DEMO_REPORT_DIR is set; empty otherwise.
REPORT=()
report_for() {
  REPORT=()
  if [[ -n "${PO_DEMO_REPORT_DIR:-}" ]]; then
    mkdir -p "$PO_DEMO_REPORT_DIR"
    REPORT=(--report "$PO_DEMO_REPORT_DIR/report-$1.json")
  fi
}

export ALLOW_PAID_MODEL_CALLS=false
unset FOUNDRY_PROJECT_ENDPOINT AZURE_CLIENT_ID APPLICATIONINSIGHTS_CONNECTION_STRING || true

PGTMP=""
cleanup() {
  if [[ -n "$PGTMP" && -d "$PGTMP" ]]; then
    pg_ctl -D "$PGTMP/data" -m immediate -w stop >/dev/null 2>&1 || true
    rm -rf "$PGTMP"
    echo "[CLEANUP] stopped and deleted the temporary PostgreSQL cluster"
  fi
}
trap cleanup EXIT INT TERM

if [[ "$MODE" == sqlite || "$MODE" == both ]]; then
  echo "== SQLite"
  report_for sqlite
  "$PYTHON" -I "$DRIVER" --backend sqlite ${REPORT[@]+"${REPORT[@]}"} "$@" || STATUS=1
fi

if [[ "$MODE" == postgres || "$MODE" == both ]]; then
  echo "== PostgreSQL"
  command -v initdb >/dev/null && command -v pg_ctl >/dev/null \
    || { echo "[BLOCKED] initdb and pg_ctl are required for the PostgreSQL run" >&2; exit 2; }
  if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    echo "[BLOCKED] port $PORT is in use; set PO_DEMO_PG_PORT" >&2; exit 2
  fi
  PGTMP="$(mktemp -d "${TMPDIR:-/tmp}/po-demo-pg.XXXXXX")"
  initdb -D "$PGTMP/data" -U postgres --auth=trust -E UTF8 >/dev/null
  pg_ctl -D "$PGTMP/data" -l "$PGTMP/server.log" -w \
    -o "-p $PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=''" start >/dev/null
  report_for postgres
  "$PYTHON" -I "$DRIVER" --backend postgres ${REPORT[@]+"${REPORT[@]}"} \
    --pg-admin-url "postgresql+psycopg://postgres@127.0.0.1:$PORT/postgres" "$@" || STATUS=1
fi

exit "$STATUS"
