# AgroLead Assistant (FastAPI Architecture)

Система лид-обработки для ООО «Петрохлеб-Кубань» с архитектурой под ВКР:

- публичный клиентский чат;
- полноценная админ-панель;
- редактирование системного промпта по категориям;
- управление номенклатурой (демо-каталог зерновых) через админ-панель;
- автоматический сбор лидов из чата и работа с воронкой лидов в админке;
- хранение сценариев, статистики и истории чатов;
- локальная LLM через Ollama.

## Архитектура

- `webui (nginx)` — фронтенд и reverse-proxy.
- `db (PostgreSQL)` — основное хранилище данных (клиенты/чаты/лиды/номенклатура).
- `api (FastAPI)` — бизнес-логика, guardrails, admin API, статистика.
- `ollama` — инференс локальной модели.
- `picoclaw` (опционально, profile `picoclaw`) — вспомогательный сервис интеграционного контура.

Связность сервисов:

- `webui -> /api/* -> api` через proxy в [web/nginx.conf](agrolead-assistant/web/nginx.conf).
- `api -> db` через `DATABASE_URL` в [agrolead-assistant/.env.example](agrolead-assistant/.env.example).
- `api -> ollama` через `OLLAMA_BASE` в [agrolead-assistant/.env.example](agrolead-assistant/.env.example).
- `api -> GigaChat` (опционально) через `GIGACHAT_*` в [agrolead-assistant/.env.example](agrolead-assistant/.env.example).
- `picoclaw -> ollama` через `OLLAMA_BASE_URL` в [agrolead-assistant/docker-compose.yml](agrolead-assistant/docker-compose.yml).

## Структура

- `backend/app/main.py` — API приложения.
- `backend/app/models.py` — модели БД.
- `backend/app/seed.py` — дефолтные данные компании/контактов/промптов.
- `web/index.html` — клиентский чат.
- `web/admin.html` — админ-панель.
- `web/nginx.conf` — TLS + proxy (`/api/*` -> FastAPI).
- `docker-compose.yml` — оркестрация сервисов.

## Быстрый запуск

1. Скопируй env:

```bash
cp .env.example .env
```

2. Запусти деплой:

```bash
chmod +x deploy/deploy.sh
bash deploy/deploy.sh
```

Для запуска профиля Picoclaw:

```bash
ENABLE_PICOCLAW=1 bash deploy/deploy.sh
```

Если у вас другой реестр/тег Picoclaw, переопределите [`PICOCLAW_IMAGE`](agrolead-assistant/.env.example:12).

Скрипт [`deploy.sh`](agrolead-assistant/deploy/deploy.sh) выполняет последовательные smoke-проверки:

- `git fetch/pull --ff-only` перед запуском контейнеров;
- автомиграцию legacy `.env` (`127.0.0.1` -> сервисные имена `db`/`ollama`);
- подробный лог деплоя в `deploy/logs/deploy_YYYYMMDD_HHMMSS.log`;
- диагностику контейнеров и хвосты логов при любой ошибке;
- предупреждения по потенциальным рискам (`ADMIN_PASS`, `ADMIN_TOKEN`, `POSTGRES_PASSWORD` по умолчанию).

- [`/api/health`](agrolead-assistant/backend/app/main.py:245)
- [`/api/public/bootstrap`](agrolead-assistant/backend/app/main.py:250)
- [`/api/chat/stream`](agrolead-assistant/backend/app/main.py:272)
- [`/`](agrolead-assistant/web/index.html) и [`/admin`](agrolead-assistant/web/admin.html)
- proxy `webui -> /api/health` и `webui -> /api/chat`
- DB connect (`pg_isready`)
- API -> DB connect
- API -> Ollama connect
- API `/api/chat` должен вернуть полноценный LLM-ответ, а не fallback "Сервис генерации временно недоступен"
- Picoclaw connect (если `ENABLE_PICOCLAW=1`)

3. Подтянуть модель (если не подтянулась автоматически):

```bash
docker exec -it ollama ollama pull qwen2.5:0.5b
```

## URL

- Клиентский чат: `https://<DOMAIN>/`
- Админ-панель: `https://<DOMAIN>/admin` и `https://<DOMAIN>/admin/`
- API health: `https://<DOMAIN>/api/health`

## Доступ в админку

- Логин: `admin`
- Пароль: `315920`

Конфигурируется через `.env`:

- `ADMIN_USER`
- `ADMIN_PASS`
- `ADMIN_TOKEN`

## Что редактируется в /admin

- Категории системного промпта (`identity`, `scope`, `safety`, `style`, `lead_capture`).
- Данные компании (адрес, контакты, услуги, подробные отделы).
- Сценарии диалогов.
- Готовые шаблоны сценариев для продаж.
- Номенклатура (JSON-импорт/редактирование).
- Лиды (статусы: `new`, `in_progress`, `qualified`, `closed`).
- Статистика и лента чатов.

## Контекст модели и генерация

Конфигурируется через `.env`:

- `MODEL_NUM_CTX` (по умолчанию `8192`)
- `MODEL_NUM_PREDICT` (по умолчанию `180`)

Провайдер LLM:

- `LLM_PROVIDER=auto` — при наличии `GIGACHAT_AUTH_KEY` используется GigaChat, при ошибке автоматически fallback в Ollama;
- `LLM_PROVIDER=gigachat` — только GigaChat;
- `LLM_PROVIDER=ollama` — только Ollama.

Параметры GigaChat (опционально):

- `GIGACHAT_AUTH_KEY`
- `GIGACHAT_SCOPE` (обычно `GIGACHAT_API_PERS`)
- `GIGACHAT_OAUTH_URL`
- `GIGACHAT_API_URL`
- `GIGACHAT_MODEL`
- `GIGACHAT_VERIFY_SSL`

Используется в запросе к Ollama для увеличенного контекстного окна в пределах ресурсов хоста.

Критично: не храните `GIGACHAT_AUTH_KEY` в git. Ключ должен быть только в серверном `.env`.

## Встроенные guardrails

- блокировка оффтопа и вредоносных запросов;
- запрет генерации небезопасного контента;
- фокус на бизнес-домене (зерновая продукция, логистика, заявки);
- ассистент всегда ведёт диалог к квалификации лида.

## SSL

Для HTTPS положи:

- `ssl/fullchain.pem`
- `ssl/privkey.key`

Контейнер `webui` использует их в `web/nginx.conf`.

Если сертификаты отсутствуют, `deploy.sh` автоматически переключает web-smoke проверки на HTTP (`http://127.0.0.1`).
