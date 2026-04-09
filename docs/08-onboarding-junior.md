# 08. Онбординг для джуна: как быстро понять легаси

Цель: за 1-2 дня войти в контекст и не бояться вносить изменения.

## День 0 (60-90 минут): собрать картину целиком

1. Прочитай `docs/01-overview.md`.
2. Прочитай `docs/02-architecture.md`.
3. Открой `backend/app/main.py` и найди `_process_chat`.
4. Открой `web/index.html` и `web/admin.html`, чтобы понять UI-потоки.

Результат: ты понимаешь, какие сущности и потоки вообще существуют.

## День 1: понять диалог и данные

### Шаг 1. Диалоговая логика

- Прочитай `docs/03-dialogue-and-prompts.md`.
- Затем открой файлы:
  - `backend/app/sales_logic.py`
  - `backend/app/guardrails.py`
  - `backend/app/agent.py`
  - `backend/app/tools/response_tool.py`

Что нужно уметь объяснить после чтения:

- как определяется request type;
- какие факты извлекаются;
- когда лид становится `partially_qualified` и `qualified`;
- как формируется prompt для LLM.

### Шаг 2. Модель БД

- Прочитай `docs/04-database.md`.
- Затем открой:
  - `backend/app/models.py`
  - `backend/app/seed.py`

Что нужно уметь объяснить:

- какие таблицы участвуют в чате;
- какие таблицы участвуют в CRM;
- как seed заполняет справочники.

## День 2: API и эксплуатация

### Шаг 3. API и админка

- Прочитай `docs/05-api.md`.
- Привяжи endpoint'ы к экранам `web/admin.html`.

### Шаг 4. Деплой

- Прочитай `docs/06-deploy-and-ops.md`.
- Просмотри `deploy.sh` и пойми порядок smoke-тестов.

## Быстрый чек-лист перед первой задачей

- Понимаю путь данных: `chat_message -> chat_extracted_fact -> crm_lead`.
- Знаю, где менять extraction (`sales_logic.py`).
- Знаю, где менять тексты guardrails (`guardrail_response_policy.py`).
- Знаю, где менять prompt поведение (`response_tool.py`, `agent.py`).
- Понимаю, какой endpoint используется в UI.

## Как дебажить проблему "ответ странный"

1. Проверить, не сработал ли guardrails.
2. Проверить `request_type` в ответе API.
3. Проверить `captured_fields` и `chat_extracted_fact`.
4. Проверить `missing_field` и `next_question`.
5. Проверить, что ушло в prompt (через debug mode / временный лог).
6. Проверить статус LLM (`/api/llm/status`).

## Как дебажить проблему "лид не собирается"

1. Открыть workspace endpoint:
   - `GET /api/v1/admin/leads/{lead_id}/workspace`
2. Проверить:
   - `facts`
   - `missing_fields`
   - `checkpoints`
3. Проверить required fields для request type в `sales_logic.py`.
4. Проверить confidence пороги (`_fact_is_collected`).

## Первые безопасные задачи для джуна

1. Добавить новый алиас культуры/региона в `sales_logic.py`.
2. Добавить/уточнить guardrail reply-варианты.
3. Добавить pydantic-схему для одного CRUD endpoint.
4. Добавить тест-кейс в `backend/tests/test_integration_dialogue.py`.

## Частые ошибки новичков

- Меняют только UI-настройки, ожидая изменение backend-логики.
- Не проверяют влияние confidence на собранность полей.
- Путают `status_code` лида и `current_state_code` чат-сессии.
- Не замечают, что часть endpoint'ов — alias для совместимости.

## Куда смотреть при архитектурном рефакторинге

- План эволюции: `docs/07-legacy-and-improvements.md`.
- Рефакторинг начинать с декомпозиции `main.py`, не с косметики UI.
