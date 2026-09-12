#!/usr/bin/env bash
# One-shot dev setup on macOS / Linux / WSL. Run from the repo root:
#     bash scripts/setup.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== python venv =="
[ -d .venv ] || python3 -m venv .venv
PY=./.venv/bin/python

"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install -r engine/requirements.txt --quiet
"$PY" -m pip install -r server/requirements.txt --quiet
"$PY" -m pip install -e ./engine --quiet

echo "== web deps =="
(cd web && npm install --no-audit --no-fund)

if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example — add your API keys"
fi

echo "== git =="
command -v git >/dev/null && echo "git: $(command -v git)" || echo "git NOT FOUND — install it"

cat <<'EOF'

Done. Next:
  1. put your API keys in .env
  2. cd engine && ../.venv/bin/python -m engine.cli doctor
  3. bash scripts/dev.sh
EOF
