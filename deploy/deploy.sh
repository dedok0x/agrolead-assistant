#!/usr/bin/env bash
set -Eeuo pipefail

# ==========================================
# AGROLEAD / NANOCLAW AGGRESSIVE DEPLOY
# ==========================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE_DOT="$ROOT_DIR/.env.example"
ENV_EXAMPLE_PLAIN="$ROOT_DIR/env.example"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
LOG_DIR="$ROOT_DIR/deploy/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/deploy_$(date +%Y%m%d_%H%M%S).log"

exec > >(tee -a "$LOG_FILE") 2>&1

GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[1;34m'
NC='\033[0m'

step() { echo -e "${BLUE}[STEP]${NC} $1"; }
ok() { echo -e "${GREEN}[OK]${NC} $1"; }
die() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Команда '$1' не найдена"
}

wait_http() {
  local url="$1"
  local tries="${2:-40}"
  local sleep_s="${3:-2}"
  local i
  for i in $(seq 1 "$tries"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_s"
  done
  return 1
}

on_error() {
  local code=$?
  echo ""
  echo "[DIAG] deploy crashed (code=$code)"
  docker compose -f "$COMPOSE_FILE" ps || true
  echo ""
  docker compose -f "$COMPOSE_FILE" logs --tail 120 api || true
  echo ""
  docker compose -f "$COMPOSE_FILE" logs --tail 120 nanoclaw || true
  echo ""
  echo "[DIAG] full log: $LOG_FILE"
  exit "$code"
}
trap on_error ERR

step "Проверка зависимостей"
require_cmd git
require_cmd docker
require_cmd curl
require_cmd npm
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin недоступен"
ok "Базовые зависимости готовы"

cd "$ROOT_DIR"

step "Агрессивная очистка старого PicoClaw окружения"
docker compose -f "$COMPOSE_FILE" down -v --rmi all --remove-orphans || true
docker rm -f picoclaw agrolead-picoclaw 2>/dev/null || true
docker image rm ghcr.io/sipeed/picoclaw:latest 2>/dev/null || true
rm -rf ./picoclaw* ./data/picoclaw* ./config/picoclaw* ./tmp/picoclaw* || true
ok "Старое PicoClaw окружение удалено"

step "Жёсткий reset репозитория"
git fetch --all --prune
git reset --hard origin/main
git clean -fdx
ok "Репозиторий синхронизирован с origin/main"

step "Подготовка .env"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ENV_EXAMPLE_DOT" ]]; then
    cp "$ENV_EXAMPLE_DOT" "$ENV_FILE"
  elif [[ -f "$ENV_EXAMPLE_PLAIN" ]]; then
    cp "$ENV_EXAMPLE_PLAIN" "$ENV_FILE"
  else
    cat > "$ENV_FILE" <<'EOF'
TZ=Europe/Moscow
POSTGRES_DB=agrolead
POSTGRES_USER=agrolead
POSTGRES_PASSWORD=agrolead123
DATABASE_URL=postgresql+psycopg://agrolead:agrolead123@db:5432/agrolead
ADMIN_USER=admin
ADMIN_PASS=315920
ADMIN_TOKEN=agrolead-admin-token
LLM_PROVIDER=auto
GIGACHAT_AUTH_KEY=
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_OAUTH_URL=https://gigachat.devices.sberbank.ru/api/v2/oauth
GIGACHAT_API_URL=https://gigachat.devices.sberbank.ru/api/v1
GIGACHAT_MODEL=GigaChat-Max
GIGACHAT_VERIFY_SSL=1
GIGACHAT_TOKEN_REFRESH_SECONDS=1500
OLLAMA_BASE=http://ollama:11434
OLLAMA_MODEL=qwen2.5:72b-instruct
OLLAMA_NUM_CTX=16384
OLLAMA_NUM_PREDICT=220
NANOCLAW_IMAGE=ghcr.io/qwibitai/nanoclaw:latest
NANOCLAW_BASE_URL=http://nanoclaw:8788
NANOCLAW_HTTP_ADAPTER_URL=http://api:8000/api/nanoclaw/agent/chat
NANOCLAW_TIMEOUT_SECONDS=45
EOF
  fi
fi

if ! grep -Eq '^GIGACHAT_AUTH_KEY=.+' "$ENV_FILE"; then
  read -r -p "Введите GIGACHAT_AUTH_KEY (обязательно для приоритета GigaChat): " GC_KEY
  if [[ -z "${GC_KEY:-}" ]]; then
    die "GIGACHAT_AUTH_KEY пустой. Остановка деплоя."
  fi
  if grep -q '^GIGACHAT_AUTH_KEY=' "$ENV_FILE"; then
    sed -i "s|^GIGACHAT_AUTH_KEY=.*$|GIGACHAT_AUTH_KEY=${GC_KEY}|" "$ENV_FILE"
  else
    echo "GIGACHAT_AUTH_KEY=${GC_KEY}" >> "$ENV_FILE"
  fi
fi
ok ".env готов"

step "Локальная установка зависимостей backend"
python -m pip install --upgrade pip
python -m pip install -r "$ROOT_DIR/backend/requirements.txt"
ok "Python зависимости установлены"

step "NanoClaw setup (npm install + npx setup)"
TMP_NANO="$ROOT_DIR/.tmp/nanoclaw-runtime"
mkdir -p "$TMP_NANO"
if [[ ! -f "$TMP_NANO/package.json" ]]; then
  npm init -y --prefix "$TMP_NANO" >/dev/null 2>&1
fi
npm install --prefix "$TMP_NANO" @qwibitai/nanoclaw@latest
npx --yes --prefix "$TMP_NANO" @qwibitai/nanoclaw@latest setup --non-interactive || true
ok "NanoClaw runtime подготовлен"

step "Сборка и запуск Docker стека"
docker compose -f "$COMPOSE_FILE" build --no-cache
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans
ok "Контейнеры запущены"

step "Smoke tests"
API_BASE="http://127.0.0.1:8000"
WEB_BASE="http://127.0.0.1"

wait_http "$API_BASE/api/health" 60 2 || die "api/health не поднялся"

curl -fsS "$API_BASE/api/health" >/tmp/agro_health.json || die "health fail"
curl -fsS "$API_BASE/api/llm/status" >/tmp/agro_llm_status.json || die "llm/status fail"
curl -fsS -H "Content-Type: application/json" -d '{"text":"Пшеница 3 класс 100 тонн в Краснодар"}' "$API_BASE/api/chat/dry-run" >/tmp/agro_dry.json || die "chat/dry-run fail"
curl -fsS -H "Content-Type: application/json" -d '{"text":"Нужен прайс по пшенице","context":"deploy-smoke"}' "$API_BASE/api/nanoclaw/agent/chat" >/tmp/agro_nano_adapter.json || die "nanoclaw adapter fail"

grep -q '"last_provider"' /tmp/agro_llm_status.json || die "llm/status не содержит last_provider"
grep -q '"text"' /tmp/agro_dry.json || die "dry-run без text"
grep -q '"provider"' /tmp/agro_nano_adapter.json || die "nanoclaw adapter без provider"

curl -fsS "$WEB_BASE/" >/tmp/agro_web_index.html || die "web index fail"
curl -fsS "$WEB_BASE/admin" >/tmp/agro_web_admin.html || die "web admin fail"

ok "Smoke tests пройдены"

echo ""
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}           DEPLOY SUCCESS ✅                 ${NC}"
echo -e "${GREEN}=============================================${NC}"
echo "Public UI : http://127.0.0.1/"
echo "Admin UI  : http://127.0.0.1/admin"
echo "API docs  : http://127.0.0.1:8000/docs"
echo "Report    : $LOG_FILE"

