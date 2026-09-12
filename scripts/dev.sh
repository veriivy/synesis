#!/usr/bin/env bash
# Run the backend and frontend together. Ctrl-C stops both.
#     bash scripts/dev.sh            # live agents
#     REPLAY=1 bash scripts/dev.sh   # replay fixtures/stream.jsonl, no API keys
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
PY="$ROOT/.venv/bin/python"

[ -x "$PY" ] || { echo "No .venv — run bash scripts/setup.sh first."; exit 1; }

if [ "${REPLAY:-}" = "1" ]; then
  export REPLAY_FIXTURE="fixtures/stream.jsonl"
  echo "REPLAY MODE"
fi

trap 'kill 0' EXIT
(cd server && "$PY" -m uvicorn server.main:app --reload --port 8000) &
(cd web && npm run dev) &

echo "api  -> http://localhost:8000/health"
echo "web  -> http://localhost:3000"
echo
echo "The web app calls the API directly, so http://localhost:3000 must be in"
echo "CORS_ORIGINS in .env. It is by default."
wait
