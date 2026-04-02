#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE_DOT="$ROOT_DIR/.env.example"
ENV_EXAMPLE_PLAIN="$ROOT_DIR/env.example"
LOG_DIR="${TMPDIR:-/tmp}/agrolead-deploy-logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/deploy_$(date +%Y%m%d_%H%M%S).log"
LAST_RESPONSE_FILE="$LOG_DIR/last_response.json"

LAST_REQUEST_DESC=""
LAST_REQUEST_BODY=""
LAST_HTTP_CODE=""

exec > >(tee -a "$LOG_FILE") 2>&1

if [[ -t 1 ]]; then
  GREEN='\033[0;32m'
  RED='\033[0;31m'
  BLUE='\033[1;34m'
  YELLOW='\033[1;33m'
  NC='\033[0m'
else
  GREEN=''
  RED=''
  BLUE=''
  YELLOW=''
  NC=''
fi

step() {
  printf "%b[STEP]%b %s\n" "$BLUE" "$NC" "$1"
}

ok() {
  printf "%b[OK]%b %s\n" "$GREEN" "$NC" "$1"
}

warn() {
  printf "%b[WARN]%b %s\n" "$YELLOW" "$NC" "$1"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf "%b[ERROR]%b Команда '%s' не найдена\n" "$RED" "$NC" "$1"
    exit 1
  }
}

wait_http() {
  local url="$1"
  local tries="${2:-60}"
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

show_service_logs() {
  local service="$1"
  local lines="${2:-50}"
  local container_id
  container_id="$(docker compose -f "$COMPOSE_FILE" ps -q "$service" 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    warn "Логи $service: контейнер не найден"
    return 0
  fi
  echo "----- docker logs --tail=${lines} ${service} -----"
  docker logs --tail="$lines" "$container_id" 2>&1 || true
  echo "--------------------------------------------------"
}

show_all_logs() {
  local lines="${1:-100}"
  local services=(db api webui)
  local service
  for service in "${services[@]}"; do
    show_service_logs "$service" "$lines"
  done
}

show_last_request() {
  if [[ -z "$LAST_REQUEST_DESC" ]]; then
    return
  fi
  echo "----- failing request -----"
  echo "description: $LAST_REQUEST_DESC"
  echo "http_code: ${LAST_HTTP_CODE:-n/a}"
  echo "request_body: ${LAST_REQUEST_BODY:-n/a}"
  if [[ -f "$LAST_RESPONSE_FILE" ]]; then
    echo "response_body:"
    cat "$LAST_RESPONSE_FILE" || true
  fi
  echo "---------------------------"
}

die() {
  printf "%b[ERROR]%b %s\n" "$RED" "$NC" "$1"
  show_last_request
  show_all_logs 120
  echo "Полный лог деплоя: $LOG_FILE"
  exit 1
}

on_error() {
  local code=$?
  local line="${BASH_LINENO[0]:-unknown}"
  local command="${BASH_COMMAND:-unknown}"
  trap - ERR
  printf "%b[ERROR]%b Сбой на строке %s: %s\n" "$RED" "$NC" "$line" "$command"
  show_last_request
  show_all_logs 120
  echo "Полный лог деплоя: $LOG_FILE"
  exit "$code"
}

trap on_error ERR

upsert_env_var() {
  local key="$1"
  local value="$2"
  "$PYTHON_BIN" - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

lines = []
if path.exists():
    lines = path.read_text(encoding="utf-8").splitlines()

updated = False
for index, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[index] = f"{key}={value}"
        updated = True
        break

if not updated:
    lines.append(f"{key}={value}")

path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

get_env_var() {
  local key="$1"
  local value
  value="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -n 1 | cut -d'=' -f2- || true)"
  echo "$value"
}

env_or_default() {
  local key="$1"
  local fallback="$2"
  local value
  value="$(get_env_var "$key")"
  if [[ -z "$value" ]]; then
    echo "$fallback"
  else
    echo "$value"
  fi
}

prompt_secret_if_empty() {
  local key="$1"
  local prompt="$2"
  local hidden="${3:-0}"
  local current
  current="$(get_env_var "$key")"
  if [[ -n "$current" ]]; then
    return 0
  fi

  if [[ ! -t 0 ]]; then
    die "Не найден $key и нет интерактивного ввода. Заполните .env вручную."
  fi

  local input=""
  if [[ "$hidden" == "1" ]]; then
    read -r -s -p "$prompt: " input
    echo ""
  else
    read -r -p "$prompt: " input
  fi

  if [[ -z "$input" ]]; then
    die "$key пустой. Деплой остановлен."
  fi
  upsert_env_var "$key" "$input"
}

request_json() {
  local description="$1"
  local url="$2"
  local body="$3"

  LAST_REQUEST_DESC="$description"
  LAST_REQUEST_BODY="$body"
  LAST_HTTP_CODE="$(curl -sS -o "$LAST_RESPONSE_FILE" -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -d "$body" \
    "$url" || true)"
}

request_get() {
  local description="$1"
  local url="$2"
  local header_name="${3:-}"
  local header_value="${4:-}"

  LAST_REQUEST_DESC="$description"
  LAST_REQUEST_BODY="(GET)"
  if [[ -n "$header_name" ]]; then
    LAST_HTTP_CODE="$(curl -sS -o "$LAST_RESPONSE_FILE" -w "%{http_code}" -H "$header_name: $header_value" "$url" || true)"
  else
    LAST_HTTP_CODE="$(curl -sS -o "$LAST_RESPONSE_FILE" -w "%{http_code}" "$url" || true)"
  fi
}

step "Проверка окружения"
require_cmd docker
require_cmd curl
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin не найден"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  die "Python не найден"
fi
ok "Окружение готово"

cd "$ROOT_DIR"

step "Подготовка .env"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ENV_EXAMPLE_DOT" ]]; then
    cp "$ENV_EXAMPLE_DOT" "$ENV_FILE"
  elif [[ -f "$ENV_EXAMPLE_PLAIN" ]]; then
    cp "$ENV_EXAMPLE_PLAIN" "$ENV_FILE"
  else
    die "Не найден шаблон env (.env.example или env.example)"
  fi
fi

LLM_PROVIDER_VALUE="$(env_or_default "LLM_PROVIDER" "gigachat")"
LLM_PROVIDER_VALUE="${LLM_PROVIDER_VALUE,,}"
if [[ "$LLM_PROVIDER_VALUE" != "gigachat" && "$LLM_PROVIDER_VALUE" != "template" ]]; then
  warn "Неподдерживаемый LLM_PROVIDER=$LLM_PROVIDER_VALUE. Принудительно ставлю gigachat"
  LLM_PROVIDER_VALUE="gigachat"
fi
upsert_env_var "LLM_PROVIDER" "$LLM_PROVIDER_VALUE"
upsert_env_var "LLM_REQUEST_TIMEOUT_SECONDS" "5"
upsert_env_var "LLM_MAX_RETRIES" "1"
upsert_env_var "LLM_TEMPLATE_FALLBACK_ENABLED" "1"

if [[ "$LLM_PROVIDER_VALUE" == "gigachat" ]]; then
  prompt_secret_if_empty "GIGACHAT_AUTH_KEY" "Введите GIGACHAT_AUTH_KEY (без 'Basic ')" "1"
  upsert_env_var "GIGACHAT_SCOPE" "$(env_or_default "GIGACHAT_SCOPE" "GIGACHAT_API_PERS")"
  upsert_env_var "GIGACHAT_AUTH_URL" "$(env_or_default "GIGACHAT_AUTH_URL" "https://gigachat.devices.sberbank.ru/api/v2/oauth")"
  upsert_env_var "GIGACHAT_API_BASE_URL" "$(env_or_default "GIGACHAT_API_BASE_URL" "https://gigachat.devices.sberbank.ru/api/v1")"
  upsert_env_var "GIGACHAT_MODEL" "$(env_or_default "GIGACHAT_MODEL" "GigaChat-2")"
fi
ok ".env готов"

step "Пересборка и запуск контейнеров"
docker compose -f "$COMPOSE_FILE" down -v --remove-orphans || true
docker compose -f "$COMPOSE_FILE" build --no-cache --pull
docker compose -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans
ok "Сервисы подняты"

step "Health checks"
API_BASE="http://127.0.0.1:8000"
WEB_BASE="http://127.0.0.1"

wait_http "$API_BASE/api/health" 90 2 || die "API не поднялся: /api/health"
request_get "GET /api/health" "$API_BASE/api/health"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "/api/health вернул HTTP ${LAST_HTTP_CODE}"

HEALTH_JSON="$(cat "$LAST_RESPONSE_FILE")"
"$PYTHON_BIN" - "$HEALTH_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("status") != "ok":
    raise SystemExit(f"/api/health вернул не ok: {payload}")
if payload.get("agent_engine") != "single-agent-orchestrator":
    raise SystemExit(f"ожидался single-agent-orchestrator, получили {payload.get('agent_engine')}")
print("/api/health ok")
PY
ok "API health ok"

step "Smoke: dry-run"
DRY_RUN_REQUEST='{"text":"Нужна пшеница 3 класс 120 тонн в Краснодар"}'
request_json "POST /api/chat/dry-run" "$API_BASE/api/chat/dry-run" "$DRY_RUN_REQUEST"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "dry-run check не пройден (HTTP ${LAST_HTTP_CODE})"

DRY_RUN_JSON="$(cat "$LAST_RESPONSE_FILE")"
"$PYTHON_BIN" - "$DRY_RUN_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if not payload.get("done"):
    raise SystemExit("dry-run: done=false")
if not payload.get("text"):
    raise SystemExit("dry-run: пустой text")
print(f"dry-run ok | provider={payload.get('provider')} model={payload.get('model')}")
PY
ok "dry-run пройден"

step "Smoke: lead creation"
LEAD_1='{"text":"Интересует пшеница 3 класс","client_id":"smoke-lead","source_channel":"web"}'
request_json "POST /api/chat #1" "$API_BASE/api/chat" "$LEAD_1"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "chat #1 не пройден (HTTP ${LAST_HTTP_CODE})"

CHAT_1_JSON="$(cat "$LAST_RESPONSE_FILE")"
SESSION_ID="$($PYTHON_BIN - "$CHAT_1_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
sid = payload.get("session_id")
if not sid:
    raise SystemExit("session_id не получен")
if payload.get("state") != "qualification":
    raise SystemExit(f"ожидался state=qualification, получено {payload.get('state')}")
print(sid)
PY
)"

LEAD_2="{\"text\":\"Объем 200 тонн, доставка в Краснодарский край, отгрузка завтра\",\"session_id\":${SESSION_ID},\"client_id\":\"smoke-lead\",\"source_channel\":\"web\"}"
request_json "POST /api/chat #2" "$API_BASE/api/chat" "$LEAD_2"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "chat #2 не пройден (HTTP ${LAST_HTTP_CODE})"

LEAD_3="{\"text\":\"Контакт +7 900 123 45 67\",\"session_id\":${SESSION_ID},\"client_id\":\"smoke-lead\",\"source_channel\":\"web\"}"
request_json "POST /api/chat #3" "$API_BASE/api/chat" "$LEAD_3"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "chat #3 не пройден (HTTP ${LAST_HTTP_CODE})"

CHAT_3_JSON="$(cat "$LAST_RESPONSE_FILE")"
"$PYTHON_BIN" - "$CHAT_3_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("state") != "handoff":
    raise SystemExit(f"ожидался state=handoff, получено {payload.get('state')}")
if not payload.get("text"):
    raise SystemExit("ожидался непустой text")
print("chat #3 ok | lead qualified")
PY

ADMIN_USER_VALUE="$(env_or_default "ADMIN_USER" "admin")"
ADMIN_PASS_VALUE="$(env_or_default "ADMIN_PASS" "315920")"
LOGIN_BODY="{\"username\":\"${ADMIN_USER_VALUE}\",\"password\":\"${ADMIN_PASS_VALUE}\"}"
request_json "POST /api/admin/login" "$API_BASE/api/admin/login" "$LOGIN_BODY"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "admin login не пройден (HTTP ${LAST_HTTP_CODE})"
LOGIN_JSON="$(cat "$LAST_RESPONSE_FILE")"
ADMIN_TOKEN_VALUE="$($PYTHON_BIN - "$LOGIN_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
token = payload.get("token")
if not token:
    raise SystemExit("admin token отсутствует")
print(token)
PY
)"

request_get "GET /api/admin/leads" "$API_BASE/api/admin/leads?limit=100" "x-admin-token" "$ADMIN_TOKEN_VALUE"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "не удалось получить leads (HTTP ${LAST_HTTP_CODE})"
LEADS_JSON="$(cat "$LAST_RESPONSE_FILE")"
"$PYTHON_BIN" - "$LEADS_JSON" "$SESSION_ID" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
session_id = int(sys.argv[2])
matched = [lead for lead in payload if lead.get("session_id") == session_id]
if not matched:
    raise SystemExit("lead не найден в БД")
lead = matched[0]
if lead.get("status") != "qualified":
    raise SystemExit(f"lead status не qualified: {lead.get('status')}")
print(f"lead stored ok | id={lead.get('id')} status={lead.get('status')}")
PY
ok "Lead-сценарий пройден"

step "Проверка webui"
wait_http "$WEB_BASE/" 60 2 || die "Web UI не поднялся: /"
wait_http "$WEB_BASE/admin" 60 2 || die "Web UI не поднялся: /admin"
ok "Web UI доступен"

step "Автотесты backend"
docker compose -f "$COMPOSE_FILE" exec -T api python -m unittest -v tests/test_chat_stream.py tests/test_integration_dialogue.py
ok "Автотесты пройдены"

echo ""
echo -e "${GREEN}===========================================${NC}"
echo -e "${GREEN}✅ DEPLOY SUCCESS${NC}"
echo -e "${GREEN}===========================================${NC}"
echo "Чат:      http://localhost:80"
echo "Админка:  http://localhost:80/admin"
echo "API docs: http://localhost:8000/docs"
echo "Лог деплоя: $LOG_FILE"
echo "Готово"
