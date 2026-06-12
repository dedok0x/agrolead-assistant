# Тестирование

## Юнит- и интеграционные тесты

```bash
python3 -m venv work/.venv
work/.venv/bin/pip install -r backend/requirements.txt pytest
work/.venv/bin/pytest backend/tests -q
```

Тесты работают на sqlite (каждый файл создаёт собственную БД `test_*.db`
в текущей директории; файлы в `.gitignore`). GigaChat не нужен: ключ
отключается в setUp, ответы идут через template-policy/fallback, либо клиент
мокается `AsyncMock`.

## Состав

| Файл | Что проверяет |
| --- | --- |
| test_dialogue_intelligence.py | накопление фактов между репликами, подтверждение/отклонение uncertain-фактов, отсутствие повторных вопросов, рост score, защита request_type, пустой ввод |
| test_lead_types.py | покупка/продажа/логистика/хранение/FAQ/нерелевантное/токсичное/неполная заявка; hot_flag; маршрут → CRM-регионы |
| test_sales_assistant_flow.py | пошаговое дозаполнение неполной заявки до qualified, переключение консультация → заявка, сохранение состояния после мета-вопросов |
| test_api_contract_consistency.py | идентичность JSON и финального NDJSON, health, admin login/leads/workspace/session detail, 400/401 |
| test_dialogue_quality.py, test_agentic_dialogue.py | «сложный» пользователь: фрустрация, smalltalk, мета-диалог, опечатки |
| test_conversation_service.py, test_sales_engine.py | базовый конвейер и движок |
| test_guardrails_policy.py, test_response_validator.py, test_rag_service.py | защита и RAG |
| test_integration_dialogue.py, test_telegram_webhook.py, test_chat_stream.py | API, admin CRUD, телеграм-вебхук, dry-run |

## Smoke-сценарии (живой API)

```bash
docker compose up -d        # или uvicorn app.main:app
API_BASE_URL=http://127.0.0.1:8000 ./scripts/smoke_dialogue.sh
```

`backend/tests/smoke_dialogue.py` прогоняет 7 реальных обращений (покупка,
продажа, логистика, хранение, FAQ, неполная заявка, продолжение сессии)
и проверяет session_id, lead_id, status, known_facts, missing_fields,
qualification_score, next_action и отсутствие повторных вопросов.
Выход с кодом 1 при любой ошибке.
