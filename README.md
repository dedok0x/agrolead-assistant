# AgroLead Assistant v6

Прототип B2B ассистента и бэк-офиса для зернотрейдинга, логистики и обработки лидов.

## Полная документация

Актуальная документация переведена в раздел `docs/` и разбита по областям.

- Карта документации: `docs/README.md`
- Быстрый маршрут для нового разработчика: `docs/08-onboarding-junior.md`

## Коротко о системе

- Backend: FastAPI + SQLModel (`backend/app`)
- Frontend: Nginx + статический HTML/JS (`web`)
- База данных: PostgreSQL (в compose), SQLite как fallback
- LLM: GigaChat (для формулировки ответа)
- Деплой: Docker Compose + smoke-тесты (`deploy.sh`)

## Быстрый запуск

```bash
cp .env.example .env
bash ./deploy.sh
```

После деплоя:

- Публичный чат: `https://localhost`
- Админка: `https://localhost/admin`
- Swagger: `http://localhost:8000/docs`
