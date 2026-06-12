# Контракт чат-API

## POST /api/chat (= /api/v1/chat)

Запрос:

```json
{
  "text": "Хочу купить пшеницу",
  "session_id": 12,            // опционально: продолжить сессию
  "client_id": "web",
  "source_channel": "web_widget",
  "external_user_id": null
}
```

Ответ (единый для JSON и финального NDJSON-события):

```json
{
  "session_id": 12,
  "lead_id": 7,                  // null для faq_only/blocked-сессий
  "status": "draft",             // draft | partially_qualified | qualified | handed_to_manager | faq_only | blocked
  "state": "draft",
  "stage": "qualification",      // new | qualification | needs_discovery | ready_for_manager | handed_to_manager | irrelevant
  "next_action": "ask_volume",   // ask_* | answer_faq | handle_objection | handoff_manager | refuse_irrelevant
  "request_type": "sale_to_buyer",
  "known_facts": {"request_type": "buy_grain", "product": "пшеница"},
  "uncertain_facts": {"volume": {"value": "3000 тонн", "confidence": 0.72}},
  "missing_fields": ["volume", "region", "timing", "contact"],
  "qualification_score": 33,
  "captured_fields": ["тип заявки: покупка зерна", "культура: пшеница"],
  "text": "Какой объем рассматриваете?",
  "provider": "gigachat",        // gigachat | template-policy | fallback | guardrails
  "model": "GigaChat-2",
  "source_channel": "web_widget",
  "done": true
}
```

Ошибки: `400` — пустой `text`; `403` — чужой `session_id` (owner/channel
mismatch).

## POST /api/chat/stream

NDJSON: события `{"session_id", "token", "done": false}` посимвольно, затем
финальное событие с теми же полями, что и JSON-ответ (`token` и `text`
содержат полный текст). Финальное состояние идентично `/api/chat` —
покрыто тестом `test_api_contract_consistency.py`.

## Admin API (заголовок `x-admin-token`)

- `POST /api/v1/admin/login` → `{token}`;
- `GET /api/v1/leads` — реестр: status_code, qualification_score, lead_item
  (товар/объём/регионы), contact_snapshot, hot_flag;
- `GET /api/v1/admin/leads/{id}/workspace` — лид + сессии + сообщения + факты +
  missing fields + чекпойнты;
- `GET /api/v1/admin/chat-sessions/{id}` — диалог сессии целиком;
- `GET /api/health` — `{status, db_ok, agent_engine, telegram}` без авторизации.
