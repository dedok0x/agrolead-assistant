# AgroLead Assistant (FastAPI Architecture)

Система лид-обработки для ООО «Петрохлеб-Кубань» с архитектурой под ВКР:

- публичный клиентский чат;
- полноценная админ-панель;
- редактирование системного промпта по категориям;
- хранение сценариев, статистики и истории чатов;
- локальная LLM через Ollama.

## Архитектура

- `webui (nginx)` — фронтенд и reverse-proxy.
- `api (FastAPI)` — бизнес-логика, guardrails, admin API, статистика.
- `ollama` — инференс локальной модели.
- `sqlite` — хранение в `app_data` volume (`/data/app.db`).

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
chmod +x deploy/install_picoclaw.sh deploy/deploy.sh
bash deploy/deploy.sh
```

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
- Статистика и лента чатов.

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
