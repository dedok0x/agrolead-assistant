#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
LOG_DIR="$ROOT_DIR/deploy/logs"
mkdir -p "$LOG_DIR"
REPORT_FILE="$LOG_DIR/deploy_$(date +%Y%m%d_%H%M%S).log"

exec > >(tee -a "$REPORT_FILE") 2>&1

log() { echo "[ШАГ] $1"; }
ok() { echo "[OK] $1"; }
warn() { echo "[ПРЕДУПРЕЖДЕНИЕ] $1"; }
fail() { echo "[ОШИБКА] $1"; exit 1; }

on_error() {
  local exit_code=$?
  echo ""
  echo "[ОШИБКА] Сбой деплоя (код: $exit_code). Диагностика:"
  docker compose -f "$COMPOSE_FILE" ps || true
  echo ""
  echo "[ОШИБКА] Последние логи API (200 строк):"
  docker compose -f "$COMPOSE_FILE" logs --tail 200 api || true
  echo ""
  echo "[ОШИБКА] Последние логи WEBUI (120 строк):"
  docker compose -f "$COMPOSE_FILE" logs --tail 120 webui || true
  echo ""
  echo "[ОШИБКА] Полный отчет: $REPORT_FILE"
  exit "$exit_code"
}
trap on_error ERR

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Не найдена команда: $1"
}

wait_http() {
  local url="$1"
  local tries="${2:-30}"
  local sleep_s="${3:-2}"
  local extra_opts="${4:-}"
  local i

  for i in $(seq 1 "$tries"); do
    if curl -fsS $extra_opts "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_s"
  done
  return 1
}

log "Старт деплоя. Отчет: $REPORT_FILE"
log "Проверка зависимостей"
require_cmd git
require_cmd docker
require_cmd curl
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin недоступен"
ok "git, docker, docker compose и curl доступны"

log "Проверка файлов проекта"
[[ -f "$COMPOSE_FILE" ]] || fail "Не найден $COMPOSE_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  [[ -f "$ROOT_DIR/.env.example" ]] || fail "Не найдено ни $ENV_FILE, ни .env.example"
  cp "$ROOT_DIR/.env.example" "$ENV_FILE"
  ok "Создан .env из .env.example"
fi

cd "$ROOT_DIR"

if [[ -n "$(git status --porcelain)" ]]; then
  warn "В репозитории есть незакоммиченные изменения. git pull может завершиться конфликтом."
fi

log "Подтягивание изменений из git"
git fetch --all --prune
git pull --ff-only
ok "Изменения из git подтянуты"

log "Проверка рисков конфигурации"
if grep -Eq '^ADMIN_PASS=315920$' "$ENV_FILE"; then
  warn "Используется пароль админа по умолчанию (ADMIN_PASS=315920)."
fi
if grep -Eq '^ADMIN_TOKEN=agrolead-admin-token$' "$ENV_FILE"; then
  warn "Используется ADMIN_TOKEN по умолчанию."
fi
if grep -Eq '^POSTGRES_PASSWORD=agrolead123$' "$ENV_FILE"; then
  warn "Используется пароль БД по умолчанию."
fi
if [[ ! -f "$ROOT_DIR/ssl/fullchain.pem" || ! -f "$ROOT_DIR/ssl/privkey.key" ]]; then
  warn "SSL-сертификаты не найдены (ssl/fullchain.pem, ssl/privkey.key). Проверки web будут идти через HTTP."
fi
ok "Проверка рисков завершена"

log "Подъём контейнеров"
docker compose -f "$COMPOSE_FILE" pull db api ollama webui || true
if [[ "${ENABLE_PICOCLAW:-0}" == "1" ]]; then
  docker compose -f "$COMPOSE_FILE" --profile picoclaw pull picoclaw || true
fi
docker compose -f "$COMPOSE_FILE" up -d --build --remove-orphans
if [[ "${ENABLE_PICOCLAW:-0}" == "1" ]]; then
  docker compose -f "$COMPOSE_FILE" --profile picoclaw up -d picoclaw
fi
ok "Контейнеры подняты"

MODEL_NAME=$(grep '^MODEL_NAME=' "$ENV_FILE" | cut -d '=' -f2- || true)
if [[ -n "${MODEL_NAME:-}" ]]; then
  log "Проверка/загрузка модели Ollama: $MODEL_NAME"
  docker exec ollama ollama pull "$MODEL_NAME" || fail "Не удалось загрузить модель $MODEL_NAME"
  ok "Модель Ollama доступна"
fi

log "Smoke: состояния контейнеров"
docker compose -f "$COMPOSE_FILE" ps

API_BASE="http://127.0.0.1:8000"
WEB_BASE="https://127.0.0.1"
WEB_CURL_OPTS="-k"
if [[ ! -f "$ROOT_DIR/ssl/fullchain.pem" || ! -f "$ROOT_DIR/ssl/privkey.key" ]]; then
  WEB_BASE="http://127.0.0.1"
  WEB_CURL_OPTS=""
fi

log "Ожидание готовности API"
wait_http "$API_BASE/api/health" 45 2 || fail "API не стал доступен в течение 90 секунд"
ok "API готов"

log "Smoke: API health"
curl -fsS "$API_BASE/api/health" >/tmp/agro_health.json || fail "api/health недоступен"
ok "api/health"

log "Smoke: API bootstrap"
curl -fsS "$API_BASE/api/public/bootstrap" >/tmp/agro_bootstrap.json || fail "api/public/bootstrap недоступен"
ok "api/public/bootstrap"

log "Smoke: API chat stream"
curl -fsS -H "Content-Type: application/json" \
  -d '{"text":"Интересует пшеница 3 класс, объем 100 тонн","client_id":"smoke"}' \
  "$API_BASE/api/chat/stream" >/tmp/agro_chat.ndjson || fail "api/chat/stream недоступен"
grep -q '"done": true' /tmp/agro_chat.ndjson || fail "api/chat/stream не вернул done=true"
ok "api/chat/stream"

log "Smoke: Web index и admin"
curl -fsS $WEB_CURL_OPTS "$WEB_BASE/" >/tmp/agro_web_index.html || fail "web index недоступен"
curl -fsS $WEB_CURL_OPTS "$WEB_BASE/admin" >/tmp/agro_web_admin.html || fail "web admin недоступен"
ok "web index/admin"

log "Smoke: Web proxy -> API"
curl -fsS $WEB_CURL_OPTS "$WEB_BASE/api/health" >/tmp/agro_web_api_health.json || fail "proxy /api/health недоступен"
curl -fsS $WEB_CURL_OPTS -H "Content-Type: application/json" \
  -d '{"text":"Интересует пшеница 4 класс, объем 80 тонн","client_id":"smoke-web"}' \
  "$WEB_BASE/api/chat" >/tmp/agro_web_chat.json || fail "proxy /api/chat недоступен"
ok "proxy /api/health + /api/chat"

log "Smoke: DB connect"
docker compose -f "$COMPOSE_FILE" exec -T db \
  pg_isready -U "${POSTGRES_USER:-agrolead}" -d "${POSTGRES_DB:-agrolead}" \
  >/tmp/agro_db_check.txt || fail "БД не отвечает"
ok "DB connect"

log "Smoke: API -> DB connect"
docker compose -f "$COMPOSE_FILE" exec -T api python -c "from app.db import engine; from sqlalchemy import text; c=engine.connect(); c.execute(text('SELECT 1')); c.close()" \
  >/tmp/agro_api_db_check.txt || fail "API не может подключиться к БД"
ok "API -> DB connect"

log "Smoke: API -> Ollama connect"
docker compose -f "$COMPOSE_FILE" exec -T api python -c "import urllib.request; urllib.request.urlopen('http://ollama:11434/api/tags', timeout=20)" \
  >/tmp/agro_api_ollama_check.txt || fail "API не видит Ollama"
ok "API -> Ollama connect"

if [[ "${ENABLE_PICOCLAW:-0}" == "1" ]]; then
  log "Smoke: Picoclaw connect"
  docker compose -f "$COMPOSE_FILE" ps picoclaw | grep -q "picoclaw" || fail "Picoclaw контейнер не найден"
  docker compose -f "$COMPOSE_FILE" ps picoclaw | grep -Eq "Up|healthy" || fail "Picoclaw не в состоянии Up/healthy"
  ok "Picoclaw контейнер активен"
fi

ok "Деплой и проверки завершены успешно"
echo "[ОТЧЕТ] $REPORT_FILE"
