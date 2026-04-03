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

step() { printf "%b[STEP]%b %s\n" "$BLUE" "$NC" "$1"; }
ok() { printf "%b[OK]%b %s\n" "$GREEN" "$NC" "$1"; }
warn() { printf "%b[WARN]%b %s\n" "$YELLOW" "$NC" "$1"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf "%b[ERROR]%b Команда '%s' не найдена\n" "$RED" "$NC" "$1"
    exit 1
  }
}

show_service_logs() {
  local service="$1"
  local lines="${2:-120}"
  local cid
  cid="$(docker compose -f "$COMPOSE_FILE" ps -q "$service" 2>/dev/null || true)"
  if [[ -z "$cid" ]]; then
    warn "Логи $service: контейнер не найден"
    return
  fi
  echo "----- docker logs --tail=${lines} ${service} -----"
  docker logs --tail="$lines" "$cid" 2>&1 || true
  echo "--------------------------------------------------"
}

show_all_logs() {
  show_service_logs db 120
  show_service_logs api 120
  show_service_logs webui 120
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
  show_all_logs
  echo "Полный лог деплоя: $LOG_FILE"
  exit 1
}

on_error() {
  local code=$?
  local line="${BASH_LINENO[0]:-unknown}"
  local cmd="${BASH_COMMAND:-unknown}"
  trap - ERR
  printf "%b[ERROR]%b Сбой на строке %s: %s\n" "$RED" "$NC" "$line" "$cmd"
  show_last_request
  show_all_logs
  echo "Полный лог деплоя: $LOG_FILE"
  exit "$code"
}
trap on_error ERR

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

upsert_env_var() {
  local key="$1"
  local value="$2"
  "$PYTHON_BIN" - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
done = False
for i, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[i] = f"{key}={value}"
        done = True
        break
if not done:
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
    return
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
  [[ -n "$input" ]] || die "$key пустой"
  upsert_env_var "$key" "$input"
}

request_json() {
  local description="$1"
  local url="$2"
  local body="$3"
  local extra_header_name="${4:-}"
  local extra_header_value="${5:-}"

  LAST_REQUEST_DESC="$description"
  LAST_REQUEST_BODY="$body"
  if [[ -n "$extra_header_name" ]]; then
    LAST_HTTP_CODE="$(curl -sS -o "$LAST_RESPONSE_FILE" -w "%{http_code}" \
      -H "Content-Type: application/json" \
      -H "$extra_header_name: $extra_header_value" \
      -d "$body" "$url" || true)"
  else
    LAST_HTTP_CODE="$(curl -sS -o "$LAST_RESPONSE_FILE" -w "%{http_code}" \
      -H "Content-Type: application/json" \
      -d "$body" "$url" || true)"
  fi
}

request_get() {
  local description="$1"
  local url="$2"
  local extra_header_name="${3:-}"
  local extra_header_value="${4:-}"

  LAST_REQUEST_DESC="$description"
  LAST_REQUEST_BODY="(GET)"
  if [[ -n "$extra_header_name" ]]; then
    LAST_HTTP_CODE="$(curl -sS -o "$LAST_RESPONSE_FILE" -w "%{http_code}" -H "$extra_header_name: $extra_header_value" "$url" || true)"
  else
    LAST_HTTP_CODE="$(curl -sS -o "$LAST_RESPONSE_FILE" -w "%{http_code}" "$url" || true)"
  fi
}

step "Проверка окружения"
require_cmd docker
require_cmd curl
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin не найден"

if command -v python3 >/dev/null 2>&1 && python3 --version >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1 && python --version >/dev/null 2>&1; then
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
    die "Не найден шаблон env"
  fi
fi

upsert_env_var "LLM_PROVIDER" "gigachat"
upsert_env_var "LLM_REQUEST_TIMEOUT_SECONDS" "5"
upsert_env_var "LLM_MAX_RETRIES" "1"
prompt_secret_if_empty "GIGACHAT_AUTH_KEY" "Введите GIGACHAT_AUTH_KEY (без 'Basic ')" "1"
upsert_env_var "GIGACHAT_SCOPE" "$(env_or_default "GIGACHAT_SCOPE" "GIGACHAT_API_PERS")"
upsert_env_var "GIGACHAT_AUTH_URL" "$(env_or_default "GIGACHAT_AUTH_URL" "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")"
upsert_env_var "GIGACHAT_API_BASE_URL" "$(env_or_default "GIGACHAT_API_BASE_URL" "https://gigachat.devices.sberbank.ru/api/v1")"
upsert_env_var "GIGACHAT_MODEL" "$(env_or_default "GIGACHAT_MODEL" "GigaChat-2")"
upsert_env_var "GIGACHAT_VERIFY_SSL" "$(env_or_default "GIGACHAT_VERIFY_SSL" "1")"
upsert_env_var "GIGACHAT_INSECURE_SSL_FALLBACK" "$(env_or_default "GIGACHAT_INSECURE_SSL_FALLBACK" "1")"

if [[ -f "$ROOT_DIR/ssl/fullchain.pem" ]]; then
  upsert_env_var "GIGACHAT_CA_FILE" "/ssl/fullchain.pem"
elif [[ -f "$ROOT_DIR/ssl/cacert.pem" ]]; then
  upsert_env_var "GIGACHAT_CA_FILE" "/ssl/cacert.pem"
fi
ok ".env готов"

step "Проверка SSL сертификатов webui"
[[ -f "$ROOT_DIR/ssl/fullchain.pem" ]] || die "Не найден файл: ssl/fullchain.pem"
[[ -f "$ROOT_DIR/ssl/privkey.key" ]] || die "Не найден файл: ssl/privkey.key"
ok "SSL файлы webui найдены"

step "Сборка и запуск контейнеров"
docker compose -f "$COMPOSE_FILE" down -v --remove-orphans || true
docker compose -f "$COMPOSE_FILE" build --no-cache --pull
docker compose -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans
ok "Сервисы подняты"

API_BASE="http://127.0.0.1:8000"
WEB_BASE_HTTP="http://127.0.0.1"
WEB_BASE_HTTPS="https://127.0.0.1"

step "Health checks"
wait_http "$API_BASE/api/health" 90 2 || die "API не поднялся"
request_get "GET /api/health" "$API_BASE/api/health"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "/api/health вернул HTTP ${LAST_HTTP_CODE}"
HEALTH_JSON="$(cat "$LAST_RESPONSE_FILE")"
"$PYTHON_BIN" - "$HEALTH_JSON" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
if payload.get("status") != "ok":
    raise SystemExit(f"health status != ok: {payload}")
print("/api/health ok")
PY
ok "API health ok"

step "Smoke: admin login"
ADMIN_USER_VALUE="$(env_or_default "ADMIN_USER" "admin")"
ADMIN_PASS_VALUE="$(env_or_default "ADMIN_PASS" "315920")"
LOGIN_BODY="{\"username\":\"${ADMIN_USER_VALUE}\",\"password\":\"${ADMIN_PASS_VALUE}\"}"
request_json "POST /api/v1/admin/login" "$API_BASE/api/v1/admin/login" "$LOGIN_BODY"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "admin login не пройден"
ADMIN_TOKEN_VALUE="$($PYTHON_BIN - "$(cat "$LAST_RESPONSE_FILE")" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
token = payload.get("token")
if not token:
    raise SystemExit("admin token отсутствует")
print(token)
PY
)"
ok "Admin login ok"

step "Smoke: supplier lead"
SUPPLIER_REQ='{"text":"Хотим продать пшеницу 3 класс 400 тонн из Краснодара, контакт +79001112233","client_id":"smoke-supplier","source_channel":"web_widget"}'
request_json "POST /api/v1/chat supplier" "$API_BASE/api/v1/chat" "$SUPPLIER_REQ"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "supplier chat не пройден"

step "Smoke: buyer lead"
BUYER_REQ='{"text":"Нужна покупка ячменя 250 тонн в Ростов, контакт ООО АгроПлюс +79004445566","client_id":"smoke-buyer","source_channel":"web_widget"}'
request_json "POST /api/v1/chat buyer" "$API_BASE/api/v1/chat" "$BUYER_REQ"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "buyer chat не пройден"

step "Smoke: FAQ"
FAQ_REQ='{"text":"Кто вы и какие услуги оказываете?","client_id":"smoke-faq","source_channel":"web_widget"}'
request_json "POST /api/v1/chat faq" "$API_BASE/api/v1/chat" "$FAQ_REQ"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "faq chat не пройден"

step "Smoke: leads in DB via API"
request_get "GET /api/v1/leads" "$API_BASE/api/v1/leads?limit=20" "x-admin-token" "$ADMIN_TOKEN_VALUE"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "не удалось получить /api/v1/leads"
"$PYTHON_BIN" - "$(cat "$LAST_RESPONSE_FILE")" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
if len(payload) < 2:
    raise SystemExit("Лидов меньше 2 после smoke")
print(f"leads ok: {len(payload)}")
PY
ok "Lead creation подтвержден"

step "Smoke: catalog/admin endpoints"
request_get "GET /api/v1/catalog/commodities" "$API_BASE/api/v1/catalog/commodities" "x-admin-token" "$ADMIN_TOKEN_VALUE"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "catalog commodities endpoint failed"
request_get "GET /api/v1/catalog/quality-templates" "$API_BASE/api/v1/catalog/quality-templates" "x-admin-token" "$ADMIN_TOKEN_VALUE"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "catalog quality-templates endpoint failed"
request_get "GET /api/v1/admin/stats" "$API_BASE/api/v1/admin/stats" "x-admin-token" "$ADMIN_TOKEN_VALUE"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "admin stats endpoint failed"
ok "Catalog/admin endpoints ok"

step "Smoke: webui HTTPS and redirect"
HTTP_CODE_HTTP="$(curl -sS -I -o /dev/null -w "%{http_code}" "$WEB_BASE_HTTP/" || true)"
[[ "$HTTP_CODE_HTTP" == "301" || "$HTTP_CODE_HTTP" == "302" ]] || die "HTTP redirect на 443 не работает"
HTTP_CODE_HTTPS="$(curl -k -sS -o /dev/null -w "%{http_code}" "$WEB_BASE_HTTPS/" || true)"
[[ "$HTTP_CODE_HTTPS" == "200" ]] || die "HTTPS webui недоступен"
ok "Web UI redirect + HTTPS ok"

step "Smoke: webui design-system asset"
request_get "GET /assets/design-system.css" "$WEB_BASE_HTTPS/assets/design-system.css"
[[ "$LAST_HTTP_CODE" == "200" ]] || die "design-system.css недоступен"
CSS_CONTENT="$(cat "$LAST_RESPONSE_FILE")"
"$PYTHON_BIN" - "$CSS_CONTENT" <<'PY'
import sys
css = sys.argv[1]
required = ["--ds-bg", ".ds-top-nav", ".ds-table"]
missing = [token for token in required if token not in css]
if missing:
    raise SystemExit(f"design-system.css не содержит ожидаемые токены: {missing}")
print("design-system.css ok")
PY
ok "Design-system asset ok"

step "Smoke: runtime code freshness"
docker compose -f "$COMPOSE_FILE" exec -T api python - <<'PY'
import inspect

from app.main import _now, chat_dry_run
from app.sales_logic import detect_request_type

now_src = inspect.getsource(_now)
if "datetime.now(timezone.utc)" not in now_src:
    raise SystemExit("api container использует устаревший app.main (_now) — ожидается timezone-aware реализация")

dry_run_src = inspect.getsource(chat_dry_run)
if "service-unavailable" not in dry_run_src:
    raise SystemExit("api container использует устаревший chat_dry_run — нет 503 fallback guard")

if detect_request_type("Нужна логистика авто из Краснодара в Новороссийск 300 тонн") != "logistics_request":
    raise SystemExit("api container использует устаревший detect_request_type для логистики")

print("runtime code ok")
PY
ok "Runtime code freshness ok"

step "Автотесты backend"
docker compose -f "$COMPOSE_FILE" exec -T api python -m unittest -v tests/test_chat_stream.py tests/test_integration_dialogue.py
ok "Автотесты пройдены"

echo ""
echo -e "${GREEN}===========================================${NC}"
echo -e "${GREEN}✅ DEPLOY SUCCESS${NC}"
echo -e "${GREEN}===========================================${NC}"
echo "Чат:      https://localhost"
echo "Админка:  https://localhost/admin"
echo "API docs: http://localhost:8000/docs"
echo "Лог деплоя: $LOG_FILE"
echo "Готово"
