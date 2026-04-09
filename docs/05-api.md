# 05. API и контракты

## 1) Базовые принципы

- API реализован в `backend/app/main.py`.
- Формат: JSON, для стрима — NDJSON.
- Версионирование частично: есть и `v1`, и legacy alias endpoint'ы.
- Swagger: `http://localhost:8000/docs`.

## 2) Аутентификация

### Admin API

- Вход: `POST /api/v1/admin/login`.
- На успешном входе возвращается `token`.
- Токен передается в header: `x-admin-token`.
- Сессия валидируется по таблице `admin_session`.

### Public API

- Чатовые endpoint'ы доступны без admin token.
- `GET /api/public/bootstrap` доступен без авторизации.

## 3) Карта endpoint'ов

### Системные

- `GET /api/health`
- `GET /api/llm/status` (требует admin token)

### Чат

- `POST /api/v1/chat`
- `POST /api/chat` (совместимость)
- `POST /api/chat/stream` (NDJSON stream)
- `POST /api/chat/dry-run` (без сохранения в БД)

### Public bootstrap

- `GET /api/public/bootstrap`

### Admin auth

- `POST /api/admin/login` (alias)
- `POST /api/v1/admin/login`
- `POST /api/v1/admin/logout`

### Аналитика и пайплайн

- `GET /api/v1/admin/stats`
- `GET /api/admin/stats` (alias)
- `GET /api/v1/admin/pipeline`

### Лиды

- `GET /api/v1/leads`
- `GET /api/admin/leads` (alias)
- `PUT /api/v1/leads/{lead_id}`
- `PUT /api/admin/leads/{lead_id}` (alias)
- `GET /api/v1/admin/leads/{lead_id}/workspace`

### Каталоги и справочники

- `GET/POST/PUT/DELETE /api/v1/catalog/commodities`
- `GET/POST/PUT /api/v1/catalog/regions`
- `GET/POST/PUT /api/v1/catalog/transport-modes`
- `GET/POST/PUT /api/v1/catalog/delivery-basis`
- `GET/POST/PUT /api/v1/catalog/quality-templates`
- `GET/POST/PUT /api/v1/catalog/price-policies`
- `GET/POST/PUT /api/v1/catalog/lots`

### База знаний

- `GET /api/v1/knowledge` (public)
- `GET/POST/PUT /api/v1/admin/knowledge` (admin)

### Операционные сущности

- `GET/POST/PUT /api/v1/admin/counterparties`
- `GET/POST/PUT /api/v1/admin/tasks`
- `GET/POST/PUT /api/v1/admin/users`
- `GET/POST/PUT /api/v1/admin/settings`

### Read-only reference endpoint'ы

- `GET /api/v1/admin/reference/request-types`
- `GET /api/v1/admin/reference/lead-sources`
- `GET /api/v1/admin/reference/departments`
- `GET /api/v1/admin/reference/manager-roles`
- `GET /api/v1/admin/reference/counterparty-types`

## 4) Чатовый контракт

### Вход (`POST /api/v1/chat`)

```json
{
  "text": "Продажа пшеницы 3 класс 400 тонн из Краснодара, контакт +79001112233",
  "session_id": null,
  "client_id": "web-user",
  "source_channel": "web_widget",
  "external_user_id": null,
  "external_chat_id": null,
  "debug": false
}
```

### Выход (успех)

```json
{
  "session_id": 12,
  "lead_id": 34,
  "request_type": "purchase_from_supplier",
  "status": "partially_qualified",
  "state": "partially_qualified",
  "provider": "gigachat",
  "model": "GigaChat-2",
  "text": "...",
  "captured_fields": ["культура: 1", "объем: 400.0"],
  "next_action": "Дособрать критичные поля и назначить менеджера",
  "negotiation_stage": "value_hypothesis",
  "done": true
}
```

### Guardrails-ответ (блок)

```json
{
  "session_id": 44,
  "lead_id": null,
  "text": "В таком тоне диалог не продолжаю...",
  "provider": "guardrails",
  "model": "policy-v2",
  "state": "blocked",
  "guardrail": {
    "decision_code": "toxic_hard_stop",
    "severity": 3,
    "policy_tags": ["toxicity", "hard-stop"]
  },
  "done": true
}
```

## 5) Стримовый чат (`POST /api/chat/stream`)

Возвращает NDJSON, где:

- промежуточные события содержат по символу в `token`;
- финальное событие (`done=true`) содержит итоговый payload.

Пример финальной строки:

```json
{
  "session_id": 12,
  "lead_id": 34,
  "request_type": "sale_to_buyer",
  "status": "draft",
  "provider": "gigachat",
  "model": "GigaChat-2",
  "captured_fields": [],
  "next_action": "Собрать минимальный набор полей",
  "negotiation_stage": "qualification",
  "token": "Полный текст ответа",
  "done": true
}
```

## 6) Admin auth контракт

### Вход

`POST /api/v1/admin/login`

```json
{
  "username": "admin",
  "password": "315920"
}
```

### Выход

```json
{
  "token": "<session-token>"
}
```

Logout: `POST /api/v1/admin/logout` (с тем же `x-admin-token`).

## 7) Особенности контрактов легаси

- Многие CRUD endpoint'ы принимают `dict[str, Any]` без строгой pydantic-схемы.
- Есть alias endpoint'ы (`/api/admin/*`, `/api/chat`) для обратной совместимости.
- В некоторых ответах используются ID вместо человекочитаемых лейблов (например, часть полей в workspace).
- Dry-run может вернуть 503 при недоступности LLM, а обычный чат чаще возвращает 200 с fallback текстом.

## 8) Рекомендации по эволюции API

1. Ввести явные request/response модели для всех endpoint'ов.
2. Согласовать формат ошибок (единый error envelope).
3. Убрать дубли alias endpoint'ов после миграционного окна.
4. Добавить пагинацию на list endpoint'ы.
5. Добавить versioned changelog API-контрактов.
