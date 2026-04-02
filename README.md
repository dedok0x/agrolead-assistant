# AgroLead Assistant v2 (NanoClaw)

Полный рефакторинг B2B sales-assistant для ООО «Петрохлеб-Кубань».

## Что изменилось

- Полная миграция с PicoClaw на NanoClaw.
- NanoClaw вынесен в отдельный изолированный контейнер.
- Бизнес-логика осталась в FastAPI (guardrails + state-machine + лиды).
- LLM-стратегия: GigaChat (приоритет) -> Ollama (fallback only).
- `/api/llm/status` показывает реального провайдера и модель.

## Сервисы

- `webui (nginx)`
- `api (FastAPI + SQLModel)`
- `db (PostgreSQL)`
- `nanoclaw (agent engine)`
- `ollama` (опциональный профиль fallback)

Подробная схема: `docs/architecture.md`.

## Быстрый запуск

```bash
chmod +x deploy/deploy.sh
bash deploy/deploy.sh
```

Скрипт агрессивный: удаляет старые PicoClaw контейнеры/образы/данные, делает `git reset --hard`, поднимает стек заново и прогоняет smoke-тесты.

## Ключевые endpoint'ы

- `GET /api/health`
- `GET /api/llm/status`
- `POST /api/chat`
- `POST /api/chat/stream`
- `POST /api/chat/dry-run`
- `POST /api/nanoclaw/agent/chat`

## Промпты

Готовые примеры для sales/токсичности/state-machine: `docs/prompts.md`.
