#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"

echo "[1/5] Install Docker Engine + Compose plugin"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --yes --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "[2/5] Enable Docker autostart"
sudo systemctl enable docker
sudo systemctl start docker

echo "[3/5] Validate required files"
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "ERROR: $COMPOSE_FILE not found"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ROOT_DIR/.env.example" ]]; then
    echo "WARN: $ENV_FILE not found, creating from .env.example"
    cp "$ROOT_DIR/.env.example" "$ENV_FILE"
    echo "INFO: $ENV_FILE created. Please edit real values after first run."
  else
    echo "ERROR: $ENV_FILE not found and $ROOT_DIR/.env.example is missing"
    exit 1
  fi
fi

echo "[4/5] Pull and run stack (db + api + ollama + webui)"
cd "$ROOT_DIR"

docker compose pull
docker compose up -d

if [[ "${ENABLE_PICOCLAW:-0}" == "1" ]]; then
  echo "INFO: Starting optional picoclaw profile"
  docker compose --profile picoclaw up -d picoclaw
fi

MODEL_NAME=$(grep '^MODEL_NAME=' "$ENV_FILE" | cut -d '=' -f2- || true)
if [[ -n "${MODEL_NAME:-}" ]]; then
  echo "INFO: Pull local model in Ollama: $MODEL_NAME"
  docker exec ollama ollama pull "$MODEL_NAME" || true
fi

echo "[5/5] Smoke checks"
docker compose ps

echo "[SMOKE] API health"
curl -fsS http://127.0.0.1:8000/api/health >/tmp/agro_health.json
echo "OK api/health"

echo "[SMOKE] API bootstrap"
curl -fsS http://127.0.0.1:8000/api/public/bootstrap >/tmp/agro_bootstrap.json
echo "OK api/public/bootstrap"

echo "[SMOKE] API chat stream"
curl -fsS -H "Content-Type: application/json" \
  -d '{"text":"Интересует пшеница 3 класс, объем 100 тонн","client_id":"smoke"}' \
  http://127.0.0.1:8000/api/chat/stream >/tmp/agro_chat.ndjson
grep -q '"done": true' /tmp/agro_chat.ndjson
echo "OK api/chat/stream"

echo "[SMOKE] Web pages"
curl -kfsS https://127.0.0.1/ >/tmp/agro_web_index.html
curl -kfsS https://127.0.0.1/admin >/tmp/agro_web_admin.html
echo "OK web index/admin"

echo "[SMOKE] Web proxy API"
curl -kfsS https://127.0.0.1/api/health >/tmp/agro_web_api_health.json
curl -kfsS -H "Content-Type: application/json" \
  -d '{"text":"Интересует пшеница 4 класс, объем 80 тонн","client_id":"smoke-web"}' \
  https://127.0.0.1/api/chat >/tmp/agro_web_chat.json
echo "OK web proxy api"

if [[ "${ENABLE_PICOCLAW:-0}" == "1" ]]; then
  echo "[SMOKE] PicoClaw health"
  curl -fsS http://127.0.0.1:8787 >/tmp/picoclaw_health.txt || (echo "PicoClaw check failed" && exit 1)
  echo "OK picoclaw"
fi

echo "Done"
