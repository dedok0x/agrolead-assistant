#!/usr/bin/env bash
# Smoke-прогон диалоговых сценариев против работающего API.
# Использование: API_BASE_URL=http://127.0.0.1:8000 ./scripts/smoke_dialogue.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
if [ -x "work/.venv/bin/python" ]; then
  PYTHON="work/.venv/bin/python"
fi

export API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8000}"
echo "Smoke dialogue against ${API_BASE_URL}"
exec "$PYTHON" backend/tests/smoke_dialogue.py
