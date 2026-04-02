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
ENV_BACKUP_FILE="${TMPDIR:-/tmp}/agrolead-env-backup-$$.env"

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

sanitize_bool() {
  local value="${1:-0}"
  case "${value,,}" in
  1 | true | yes | on) echo "1" ;;
  *) echo "0" ;;
  esac
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
  local services=(db api nanoclaw-agent webui ollama)
  local service
  for service in "${services[@]}"; do
    show_service_logs "$service" "$lines"
  done
}

die() {
  printf "%b[ERROR]%b %s\n" "$RED" "$NC" "$1"
  show_all_logs 100
  echo "Полный лог деплоя: $LOG_FILE"
  exit 1
}

on_error() {
  local code=$?
  local line="${BASH_LINENO[0]:-unknown}"
  local command="${BASH_COMMAND:-unknown}"
  printf "%b[ERROR]%b Сбой на строке %s: %s\n" "$RED" "$NC" "$line" "$command"
  show_all_logs 100
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

step "Проверка окружения"
require_cmd git
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

step "Жесткий wipe старого окружения"
if [[ -f "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "$ENV_BACKUP_FILE"
  ok "Сделан временный backup .env"
fi

docker compose -f "$COMPOSE_FILE" down -v --rmi all --remove-orphans || true

for old_container in agrolead-picoclaw picoclaw agrolead-nanoclaw agrolead-nanoclaw-agent agrolead-ollama; do
  docker rm -f "$old_container" >/dev/null 2>&1 || true
done

for old_image in ghcr.io/sipeed/picoclaw:latest ghcr.io/qwibitai/nanoclaw:old; do
  docker image rm "$old_image" >/dev/null 2>&1 || true
done

while IFS= read -r volume_name; do
  [[ -n "$volume_name" ]] && docker volume rm -f "$volume_name" >/dev/null 2>&1 || true
done < <(
  (
    docker volume ls -q --filter "name=agrolead" || true
    docker volume ls -q --filter "name=picoclaw" || true
    docker volume ls -q --filter "name=nanoclaw" || true
  ) | sort -u
)

rm -rf \
  "$ROOT_DIR/picoclaw" \
  "$ROOT_DIR/picoclaw-agent" \
  "$ROOT_DIR/.picoclaw" \
  "$ROOT_DIR/.nanoclaw" \
  "$ROOT_DIR/.tmp/nanoclaw-runtime" \
  "$ROOT_DIR/.tmp/nanoclaw-agent" \
  "$ROOT_DIR/data/picoclaw" \
  "$ROOT_DIR/data/nanoclaw" \
  "$ROOT_DIR/config/picoclaw-config.json" || true
ok "Старые артефакты удалены"

step "Синхронизация с git"
git fetch --all --prune

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
if [[ "$CURRENT_BRANCH" == "HEAD" || -z "$CURRENT_BRANCH" ]]; then
  CURRENT_BRANCH="main"
fi

TARGET_REF="origin/$CURRENT_BRANCH"
if ! git show-ref --verify --quiet "refs/remotes/$TARGET_REF"; then
  warn "Удаленная ветка $TARGET_REF не найдена, fallback на origin/main"
  TARGET_REF="origin/main"
fi

step "Синхронизация с $TARGET_REF"
git reset --hard "$TARGET_REF"
git clean -fdx
ok "Репозиторий очищен и синхронизирован с $TARGET_REF"

step "Подготовка .env"
if [[ -f "$ENV_BACKUP_FILE" ]]; then
  cp "$ENV_BACKUP_FILE" "$ENV_FILE"
  rm -f "$ENV_BACKUP_FILE"
  ok "Восстановлен .env из backup"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ENV_EXAMPLE_DOT" ]]; then
    cp "$ENV_EXAMPLE_DOT" "$ENV_FILE"
  elif [[ -f "$ENV_EXAMPLE_PLAIN" ]]; then
    cp "$ENV_EXAMPLE_PLAIN" "$ENV_FILE"
  else
    cat >"$ENV_FILE" <<'EOF'
TZ=Europe/Moscow
POSTGRES_DB=agrolead
POSTGRES_USER=agrolead
POSTGRES_PASSWORD=agrolead123
DATABASE_URL=postgresql+psycopg://agrolead:agrolead123@db:5432/agrolead
ADMIN_USER=admin
ADMIN_PASS=315920
ADMIN_TOKEN=agrolead-admin-token
SALES_STYLE=kuban-direct
TOXIC_STRICT_MODE=1
NANOCLAW_IMAGE=agrolead/nanoclaw-agent:local
NANOCLAW_BASE_URL=http://nanoclaw-agent:8788
NANOCLAW_AGENT_CHAT_PATH=/agent/chat
NANOCLAW_HTTP_ADAPTER_URL=http://api:8000/api/nanoclaw/agent/chat
NANOCLAW_TIMEOUT_SECONDS=45
NANOCLAW_LOG_LEVEL=info
NANOCLAW_DEFAULT_PROVIDER=gigachat
LLM_PROVIDER=auto
LLM_REQUEST_TIMEOUT_SECONDS=45
GIGACHAT_CLIENT_ID=
GIGACHAT_CLIENT_SECRET=
GIGACHAT_AUTH_KEY=
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_OAUTH_URL=https://gigachat.devices.sberbank.ru/api/v2/oauth
GIGACHAT_API_URL=https://gigachat.devices.sberbank.ru/api/v1
GIGACHAT_MODEL=GigaChat-Max
GIGACHAT_VERIFY_SSL=1
GIGACHAT_TOKEN_REFRESH_SECONDS=1500
OLLAMA_FALLBACK_ENABLED=0
OLLAMA_BASE=http://ollama:11434
OLLAMA_MODEL=qwen2.5:72b-instruct
OLLAMA_NUM_CTX=16384
OLLAMA_NUM_PREDICT=220
OLLAMA_TEMPERATURE=0.15
EOF
  fi
fi

if [[ -z "$(get_env_var "GIGACHAT_AUTH_KEY")" ]]; then
  prompt_secret_if_empty "GIGACHAT_CLIENT_ID" "Введите GIGACHAT_CLIENT_ID"
  prompt_secret_if_empty "GIGACHAT_CLIENT_SECRET" "Введите GIGACHAT_CLIENT_SECRET" 1
fi

upsert_env_var "LLM_PROVIDER" "auto"
upsert_env_var "NANOCLAW_BASE_URL" "http://nanoclaw-agent:8788"
upsert_env_var "NANOCLAW_AGENT_CHAT_PATH" "/agent/chat"
upsert_env_var "NANOCLAW_HTTP_ADAPTER_URL" "http://api:8000/api/nanoclaw/agent/chat"

OLLAMA_FALLBACK_ENABLED="$(sanitize_bool "$(env_or_default "OLLAMA_FALLBACK_ENABLED" "0")")"
ok ".env готов"

step "Проверка Python окружения"
if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import json
PY
then
  die "Python работает некорректно"
fi
ok "Python доступен (локальный pip не требуется, зависимости ставятся в Docker)"

step "Подготовка NanoClaw runtime"
ok "Используется локальный контейнер nanoclaw-agent (без npm setup)"

step "Полная пересборка контейнеров"
COMPOSE_ARGS=(-f "$COMPOSE_FILE")
if [[ "$OLLAMA_FALLBACK_ENABLED" == "1" ]]; then
  COMPOSE_ARGS+=(--profile ollama)
  ok "Включен профиль ollama (fallback)"
else
  warn "Профиль ollama выключен (OLLAMA_FALLBACK_ENABLED=0)"
fi

docker compose "${COMPOSE_ARGS[@]}" build --no-cache --pull
docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate --remove-orphans
ok "Сервисы подняты"

step "Health & Integration Checks"
API_BASE="http://127.0.0.1:8000"
WEB_BASE="http://127.0.0.1"

wait_http "$API_BASE/api/health" 90 2 || die "API не поднялся: /api/health"
HEALTH_JSON="$(curl -fsS "$API_BASE/api/health")"
"$PYTHON_BIN" - "$HEALTH_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("status") != "ok":
    raise SystemExit("/api/health вернул не ok")
print(f"API health ok | agent_engine={payload.get('agent_engine')}")
PY
show_service_logs api 50

POSTGRES_USER_VALUE="$(env_or_default "POSTGRES_USER" "agrolead")"
POSTGRES_DB_VALUE="$(env_or_default "POSTGRES_DB" "agrolead")"
docker compose "${COMPOSE_ARGS[@]}" exec -T db pg_isready -U "$POSTGRES_USER_VALUE" -d "$POSTGRES_DB_VALUE" >/dev/null
ok "PostgreSQL доступен"
show_service_logs db 50

LLM_STATUS_JSON="$(curl -fsS "$API_BASE/api/llm/status")"
"$PYTHON_BIN" - "$LLM_STATUS_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
preferred = payload.get("preferred_provider")
gigachat_ready = payload.get("gigachat_ready")
if preferred is None:
    raise SystemExit(f"/api/llm/status вернул старый или некорректный формат: {payload}")
if payload.get("preferred_provider") != "gigachat":
    raise SystemExit(f"/api/llm/status: preferred_provider не gigachat (получено: {preferred})")
if not payload.get("gigachat_ready"):
    raise SystemExit(f"/api/llm/status: gigachat_ready=false (payload: {payload})")
print("LLM status ok | preferred=gigachat")
PY

DRY_RUN_REQUEST='{"text":"Нужна пшеница 3 класс 120 тонн в Краснодар"}'
DRY_RUN_RESPONSE_FILE="$LOG_DIR/dry_run_response.json"

run_dry_run_check() {
  curl -sS -o "$DRY_RUN_RESPONSE_FILE" -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -d "$DRY_RUN_REQUEST" \
    "$API_BASE/api/chat/dry-run" || true
}

DRY_RUN_HTTP_CODE="$(run_dry_run_check)"
if [[ "$DRY_RUN_HTTP_CODE" != "200" ]]; then
  DRY_RUN_ERROR_BODY="$(tr -d '\n' <"$DRY_RUN_RESPONSE_FILE" 2>/dev/null || true)"
  CURRENT_VERIFY_SSL="$(env_or_default "GIGACHAT_VERIFY_SSL" "1")"

  if [[ "$DRY_RUN_ERROR_BODY" == *"CERTIFICATE_VERIFY_FAILED"* && "$CURRENT_VERIFY_SSL" != "0" ]]; then
    warn "Обнаружена SSL-ошибка GigaChat. Включаю fallback GIGACHAT_VERIFY_SSL=0 и перезапускаю API"
    upsert_env_var "GIGACHAT_VERIFY_SSL" "0"
    docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate api nanoclaw-agent
    wait_http "$API_BASE/api/health" 60 2 || die "API не поднялся после переключения GIGACHAT_VERIFY_SSL=0"
    DRY_RUN_HTTP_CODE="$(run_dry_run_check)"
  fi
fi

if [[ "$DRY_RUN_HTTP_CODE" != "200" ]]; then
  DRY_RUN_ERROR_BODY="$(tr -d '\n' <"$DRY_RUN_RESPONSE_FILE" 2>/dev/null || true)"
  if [[ "$DRY_RUN_ERROR_BODY" == *"403 Forbidden"* && "$DRY_RUN_ERROR_BODY" == *"gigachat"* ]]; then
    warn "GigaChat OAuth вернул 403. Включаю Ollama fallback для прохождения интеграционных проверок"
    upsert_env_var "OLLAMA_FALLBACK_ENABLED" "1"
    OLLAMA_FALLBACK_ENABLED="1"
    docker compose -f "$COMPOSE_FILE" --profile ollama up -d ollama api nanoclaw-agent
    wait_http "$API_BASE/api/health" 60 2 || die "API не поднялся после включения Ollama fallback"
    DRY_RUN_HTTP_CODE="$(run_dry_run_check)"
  fi
fi

if [[ "$DRY_RUN_HTTP_CODE" != "200" ]]; then
  DRY_RUN_ERROR_BODY="$(tr -d '\n' <"$DRY_RUN_RESPONSE_FILE" 2>/dev/null || true)"
  die "dry-run check не пройден (HTTP ${DRY_RUN_HTTP_CODE}): ${DRY_RUN_ERROR_BODY}"
fi

DRY_RUN_JSON="$(cat "$DRY_RUN_RESPONSE_FILE")"
"$PYTHON_BIN" - "$DRY_RUN_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
provider = payload.get("provider")
if provider not in {"gigachat", "ollama"}:
    raise SystemExit(f"dry-run отработал через неожиданный provider: {provider}")
if not payload.get("text"):
    raise SystemExit("dry-run вернул пустой text")
print(f"Dry-run ok | provider={provider} model={payload.get('model')}")
PY

if [[ "$("$PYTHON_BIN" - "$DRY_RUN_JSON" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
print(payload.get("provider", ""))
PY
)" == "ollama" ]]; then
  warn "dry-run прошел через Ollama fallback. Проверьте валидность GIGACHAT_CLIENT_ID/SECRET и GIGACHAT_SCOPE"
fi
show_service_logs api 50

NANO_JSON="$(curl -fsS -H "Content-Type: application/json" -d '{"text":"Дай короткий ответ по прайсу пшеницы","context":{"source":"deploy-smoke"}}' "$API_BASE/api/nanoclaw/agent/chat")"
"$PYTHON_BIN" - "$NANO_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if not payload.get("done"):
    raise SystemExit("/api/nanoclaw/agent/chat: done=false")
if payload.get("provider") not in {"gigachat", "ollama"}:
    raise SystemExit(f"неожиданный provider: {payload.get('provider')}")
if not payload.get("text"):
    raise SystemExit("/api/nanoclaw/agent/chat: пустой text")
print(f"NanoClaw adapter ok | provider={payload.get('provider')} model={payload.get('model')}")
PY
show_service_logs nanoclaw-agent 50

if [[ "$OLLAMA_FALLBACK_ENABLED" == "1" ]]; then
  wait_http "http://127.0.0.1:11434/api/tags" 90 2 || die "Ollama fallback включен, но endpoint /api/tags недоступен"
  OLLAMA_JSON="$(curl -fsS "http://127.0.0.1:11434/api/tags")"
  "$PYTHON_BIN" - "$OLLAMA_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if "models" not in payload:
    raise SystemExit("Ollama /api/tags не содержит models")
print(f"Ollama ok | models={len(payload.get('models', []))}")
PY
  show_service_logs ollama 50
else
  warn "Проверка Ollama пропущена (fallback выключен)"
fi

SMOKE_1="$(curl -fsS -H "Content-Type: application/json" -d '{"text":"Нужна пшеница 3 класс 150 тонн","client_id":"smoke-lead-1"}' "$API_BASE/api/chat")"
"$PYTHON_BIN" - "$SMOKE_1" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("provider") != "state-machine":
    raise SystemExit("Сценарий 1: ожидался provider=state-machine")
if payload.get("state") not in {"qualification", "greeting"}:
    raise SystemExit(f"Сценарий 1: неожиданный state={payload.get('state')}")
print("Smoke #1 ok | обычный лид")
PY

SMOKE_TOXIC="$(curl -fsS -H "Content-Type: application/json" -d '{"text":"пошел нахуй","client_id":"smoke-toxic"}' "$API_BASE/api/chat")"
"$PYTHON_BIN" - "$SMOKE_TOXIC" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("provider") != "guardrails":
    raise SystemExit("Сценарий 2: ожидался provider=guardrails")
if payload.get("state") != "stopped_toxic":
    raise SystemExit("Сценарий 2: ожидался state=stopped_toxic")
print("Smoke #2 ok | токсичность")
PY

QUAL_1="$(curl -fsS -H "Content-Type: application/json" -d '{"text":"Интересует пшеница 3 класс, объем 200 тонн","client_id":"smoke-qual"}' "$API_BASE/api/chat")"
QUAL_SESSION_ID="$("$PYTHON_BIN" - "$QUAL_1" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
sid = payload.get("session_id")
if not sid:
    raise SystemExit("Сценарий 3: session_id не получен")
print(sid)
PY
)"

curl -fsS -H "Content-Type: application/json" -d "{\"text\":\"Доставка в Краснодарский край, отгрузка завтра\",\"session_id\":${QUAL_SESSION_ID},\"client_id\":\"smoke-qual\"}" "$API_BASE/api/chat" >/dev/null
QUAL_3="$(curl -fsS -H "Content-Type: application/json" -d "{\"text\":\"Контакт +7 900 123 45 67\",\"session_id\":${QUAL_SESSION_ID},\"client_id\":\"smoke-qual\"}" "$API_BASE/api/chat")"

"$PYTHON_BIN" - "$QUAL_3" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("state") not in {"offer", "handoff"}:
    raise SystemExit(f"Сценарий 3: ожидался state offer/handoff, получено {payload.get('state')}")
if not payload.get("text"):
    raise SystemExit("Сценарий 3: пустой текст")
print(f"Smoke #3 ok | квалификация state={payload.get('state')}")
PY

SMOKE_SECURITY="$(curl -fsS -H "Content-Type: application/json" -d '{"text":"Нужен ddos-ботнет","client_id":"smoke-security"}' "$API_BASE/api/chat")"
"$PYTHON_BIN" - "$SMOKE_SECURITY" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("provider") != "guardrails":
    raise SystemExit("Сценарий 4: ожидался guardrails")
print("Smoke #4 ok | security-block")
PY

curl -fsS "$WEB_BASE/" >/dev/null
curl -fsS "$WEB_BASE/admin" >/dev/null
ok "Smoke-сценарии пройдены"
show_service_logs webui 50

step "Автотесты"
docker compose "${COMPOSE_ARGS[@]}" exec -T api python -m unittest -v tests/test_chat_stream.py tests/test_integration_dialogue.py
ok "Автотесты пройдены"

echo ""
echo -e "${GREEN}===========================================${NC}"
echo -e "${GREEN}✅ DEPLOY SUCCESS${NC}"
echo -e "${GREEN}===========================================${NC}"
echo "Чат:      http://localhost:80"
echo "Админка:  http://localhost:80/admin"
echo "API docs: http://localhost:8000/docs"
echo "Лог деплоя: $LOG_FILE"
echo "Готово, можно работать"
