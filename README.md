# AgroLead Assistant v6 — Petrokhleb-Kuban sales backoffice

Прототип B2B ассистента и бэк-офиса для зернового трейдера и логистического оператора.

## Что реализовано

- диалоговый движок ориентирован на конвертацию в структурированные заявки;
- deterministic сбор фактов (тип запроса, культура, объем, регион, контакт и т.д.);
- хранение промежуточных данных в SQL:
  - `chat_message`
  - `chat_extracted_fact`
  - `chat_missing_field`
  - `chat_qualification_checkpoint`
- CRM слой:
  - `crm_lead`
  - `crm_lead_item`
  - `crm_lead_contact_snapshot`
  - `crm_task`
- 1C-подобные справочники и каталоги (редактируются из admin UI/API);
- admin backoffice с разделами: Dashboard, Leads, Pipeline, Counterparties, Nomenclature, Quality Templates, Price Policies, Lots, Regions, Transport, Delivery Basis, Knowledge Base, Chat Sessions, Tasks, Users, Settings.

## Архитектура

- `api` (FastAPI): чат-оркестрация, CRM/API, admin CRUD
- `db` (PostgreSQL): единый источник данных
- `webui` (Nginx + HTML/JS): клиентский чат и бэк-офис
- `llm` (GigaChat через HTTP): только для естественной формулировки ответов

## Основные API

- чат:
  - `POST /api/v1/chat`
  - `POST /api/chat` (shim)
  - `POST /api/chat/dry-run`
- лиды:
  - `GET /api/v1/leads`
  - `PUT /api/v1/leads/{lead_id}`
- каталоги:
  - `GET/POST/PUT /api/v1/catalog/commodities`
  - `GET/POST/PUT /api/v1/catalog/quality-templates`
  - `GET/POST/PUT /api/v1/catalog/price-policies`
  - `GET/POST/PUT /api/v1/catalog/lots`
  - `GET/POST/PUT /api/v1/catalog/regions`
  - `GET/POST/PUT /api/v1/catalog/transport-modes`
  - `GET/POST/PUT /api/v1/catalog/delivery-basis`
- admin:
  - `POST /api/v1/admin/login`
  - `GET /api/v1/admin/stats`
  - `GET /api/v1/admin/pipeline`
  - `GET /api/v1/admin/chat-sessions`
  - `GET /api/v1/admin/chat-sessions/{id}`
  - `GET/POST/PUT /api/v1/admin/counterparties`
  - `GET/POST/PUT /api/v1/admin/knowledge`
  - `GET/POST/PUT /api/v1/admin/tasks`
  - `GET/POST/PUT /api/v1/admin/users`
  - `GET/PUT /api/v1/admin/settings`

## Быстрый запуск

```bash
cp env.example .env
bash ./deploy.sh
```

После успешного деплоя:

- чат: `https://localhost`
- admin: `https://localhost/admin`
- docs: `http://localhost:8000/docs`

## GigaChat

Обязательные env:

```env
GIGACHAT_AUTH_KEY=<base64 key без префикса Basic>
GIGACHAT_AUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_API_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat-2
GIGACHAT_VERIFY_SSL=1
GIGACHAT_CA_FILE=/ssl/fullchain.pem
GIGACHAT_INSECURE_SSL_FALLBACK=1
```

## SSL webui

Файлы должны лежать в `./ssl`:

- `fullchain.pem`
- `privkey.key`

Nginx на `80` делает redirect на `443`.
