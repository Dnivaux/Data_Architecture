#!/usr/bin/env bash
# Lance uvicorn (port 8000) + npm dev (port 5173) en parallèle.
# Ctrl+C arrête les deux proprement.

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

cleanup() {
    echo ""
    echo "Arrêt des serveurs..."
    [[ -n "$UVICORN_PID" ]] && kill "$UVICORN_PID" 2>/dev/null
    [[ -n "$NPM_PID" ]]     && kill "$NPM_PID"     2>/dev/null
    exit 0
}
trap cleanup INT TERM

echo ""
echo "  Démarrage API FastAPI  →  http://localhost:8000/docs"
uvicorn api.main:app --reload --port 8000 &
UVICORN_PID=$!

echo "  Démarrage Frontend     →  http://localhost:5173"
cd "$ROOT/frontend" && npm run dev &
NPM_PID=$!

echo ""
echo "  Ctrl+C pour tout arrêter."
echo ""

wait "$UVICORN_PID" "$NPM_PID"
