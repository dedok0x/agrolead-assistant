#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"

log() { echo "[ШАГ] $1"; }
ok() { echo "[OK] $1"; }
fail() { echo "[ОШИБКА] $1"; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Не найдена команда: $1"
}

log "Проверка зависимостей"
require_cmd docker
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin недоступен"
ok "Docker и Compose доступны"

log "Проверка файлов проекта"
[[ -f "$COMPOSE_FILE" ]] || fail "Не найден $COMPOSE_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  [[ -f "$ROOT_DIR/.env.example" ]] || fail "Не найдено ни $ENV_FILE, ни .env.example"
  cp "$ROOT_DIR/.env.example" "$ENV_FILE"
  ok "Создан .env из .env.example"
fi

cd "$ROOT_DIR"

log "Подъём сервисов (обязательная пересборка API)"
docker compose pull db ollama webui || true
docker compose up -d --build --remove-orphans
ok "Базовые сервисы подняты"

if [[ "${ENABLE_PICOCLAW:-0}" == "1" ]]; then
  log "Запуск Picoclaw профиля"
  docker compose --profile picoclaw up -d picoclaw
  ok "Picoclaw запущен"
fi

MODEL_NAME=$(grep '^MODEL_NAME=' "$ENV_FILE" | cut -d '=' -f2- || true)
if [[ -n "${MODEL_NAME:-}" ]]; then
  log "Проверка/загрузка модели в Ollama: $MODEL_NAME"
  docker exec ollama ollama pull "$MODEL_NAME" || fail "Не удалось загрузить модель $MODEL_NAME"
  ok "Модель Ollama доступна"
fi

log "Smoke: состояния контейнеров"
docker compose ps

log "Smoke: API health"
curl -fsS http://127.0.0.1:8000/api/health >/tmp/agro_health.json || fail "api/health недоступен"
ok "api/health"

log "Smoke: API bootstrap"
curl -fsS http://127.0.0.1:8000/api/public/bootstrap >/tmp/agro_bootstrap.json || fail "api/public/bootstrap недоступен"
ok "api/public/bootstrap"

log "Smoke: API chat stream"
curl -fsS -H "Content-Type: application/json" \
  -d '{"text":"Интересует пшеница 3 класс, объем 100 тонн","client_id":"smoke"}' \
  http://127.0.0.1:8000/api/chat/stream >/tmp/agro_chat.ndjson || fail "api/chat/stream недоступен"
grep -q '"done": true' /tmp/agro_chat.ndjson || fail "api/chat/stream не вернул done=true"
ok "api/chat/stream"

log "Smoke: Web index и admin"
curl -kfsS https://127.0.0.1/ >/tmp/agro_web_index.html || fail "web index недоступен"
curl -kfsS https://127.0.0.1/admin >/tmp/agro_web_admin.html || fail "web admin недоступен"
ok "web index/admin"

log "Smoke: Web proxy -> API"
curl -kfsS https://127.0.0.1/api/health >/tmp/agro_web_api_health.json || fail "proxy /api/health недоступен"
curl -kfsS -H "Content-Type: application/json" \
  -d '{"text":"Интересует пшеница 4 класс, объем 80 тонн","client_id":"smoke-web"}' \
  https://127.0.0.1/api/chat >/tmp/agro_web_chat.json || fail "proxy /api/chat недоступен"
ok "proxy /api/health + /api/chat"

log "Smoke: DB connect"
docker compose exec -T db pg_isready -U "${POSTGRES_USER:-agrolead}" -d "${POSTGRES_DB:-agrolead}" >/tmp/agro_db_check.txt || fail "БД не отвечает"
ok "DB connect"

log "Smoke: API -> DB connect"
docker compose exec -T api python -c "from app.db import engine; from sqlalchemy import text; c=engine.connect(); c.execute(text('SELECT 1')); c.close()" >/tmp/agro_api_db_check.txt || fail "API не может подключиться к БД"
ok "API -> DB connect"

log "Smoke: API -> Ollama connect"
docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=20)" >/tmp/agro_api_ollama_check.txt || fail "API не видит Ollama"
ok "API -> Ollama connect"

if [[ "${ENABLE_PICOCLAW:-0}" == "1" ]]; then
  log "Smoke: Picoclaw connect"
  docker compose ps picoclaw | grep -q "picoclaw" || fail "Picoclaw контейнер не найден"
  docker compose ps picoclaw | grep -Eq "Up|healthy" || fail "Picoclaw не в состоянии Up/healthy"
  ok "Picoclaw контейнер активен"
fi

ok "Деплой и проверки завершены успешно"
