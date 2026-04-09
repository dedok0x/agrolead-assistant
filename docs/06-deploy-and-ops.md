# 06. Деплой и эксплуатация

## 1) Стек деплоя

- Оркестрация: Docker Compose (`docker-compose.yml`).
- Сервисы:
  - `db` (PostgreSQL 16-alpine)
  - `api` (FastAPI + Uvicorn)
  - `webui` (Nginx + статические страницы)

## 2) Требования к окружению

- Docker + Docker Compose plugin.
- `curl`.
- `python3` или `python` (используется в deploy script).
- SSL-файлы в `ssl/`:
  - `fullchain.pem`
  - `privkey.key`

## 3) Переменные окружения

Источник шаблона: `.env.example`.

Критичные:

- DB:
  - `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`
- Admin:
  - `ADMIN_USER`, `ADMIN_PASS`, `ALLOW_STATIC_ADMIN_TOKEN`, `ADMIN_SESSION_TTL_MINUTES`
- LLM/GigaChat:
  - `LLM_PROVIDER=gigachat`
  - `GIGACHAT_AUTH_KEY`
  - `GIGACHAT_AUTH_URL`
  - `GIGACHAT_API_BASE_URL`
  - `GIGACHAT_MODEL`
  - `GIGACHAT_VERIFY_SSL`
  - `GIGACHAT_CA_FILE` (при кастомном trust chain)

## 4) Стандартный порядок деплоя (`deploy.sh`)

Скрипт `deploy.sh` выполняет полный сценарий:

1. Проверяет окружение и нужные команды.
2. Готовит `.env` (копирует из шаблона, дописывает ключи).
3. Запрашивает `GIGACHAT_AUTH_KEY`, если пусто.
4. Проверяет наличие SSL-файлов для Nginx.
5. Останавливает старый стек (`docker compose down --remove-orphans`).
6. Пересобирает образы (`build --no-cache --pull`).
7. Поднимает контейнеры (`up -d --force-recreate`).
8. Выполняет health check API.
9. Выполняет smoke-тесты:
   - admin login
   - supplier/buyer/faq chat
   - проверка лидов
   - проверка каталогов и admin endpoint'ов
   - проверка HTTPS и assets webui
   - проверка "свежести" runtime-кода в контейнере
   - запуск unit/integration тестов backend
10. Печатает итоговые URL и путь к логу деплоя.

## 5) Ручной запуск без deploy.sh

```bash
cp .env.example .env
docker compose build
docker compose up -d
```

Проверки после запуска:

- `GET http://localhost:8000/api/health`
- `https://localhost`
- `https://localhost/admin`

## 6) Эксплуатационные URL

- Чат: `https://localhost`
- Админка: `https://localhost/admin`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/api/health`

## 7) Диагностика проблем

### API не поднимается

- проверить `docker compose ps`;
- проверить логи контейнера `agrolead-api`;
- проверить доступность `db` и корректность `DATABASE_URL`.

### Ошибки LLM

- проверить `GIGACHAT_AUTH_KEY`;
- проверить SSL/CA параметры (`GIGACHAT_VERIFY_SSL`, `GIGACHAT_CA_FILE`);
- проверить endpoint `GET /api/llm/status` с admin token.

### Не работает HTTPS

- проверить `ssl/fullchain.pem` и `ssl/privkey.key`;
- проверить логи `agrolead-webui`;
- убедиться, что порты `80/443` не заняты.

## 8) Важные скрипты

- `deploy/deploy.sh`
  - thin-wrapper, который вызывает root `deploy.sh`.
- `deploy/wipe_project.sh`
  - **деструктивный скрипт**: удаляет контейнеры, volume, образы и саму директорию проекта.
  - запуск только с флагом `--yes`.

## 9) Минимальный runbook для продакшен-подобного стенда

1. Перед релизом проверить `.env` и SSL.
2. Запустить `deploy.sh`.
3. Проверить smoke-кейсы и `api/health`.
4. Проверить вход в админку и создание тестовой заявки.
5. Зафиксировать версию образов и лог деплоя.

## 10) Операционные риски текущей версии

- Deploy всегда rebuild с `--no-cache` (долго и затратно).
- Нет blue/green/canary механики.
- Нет централизованных метрик/алертов.
- Нет встроенной стратегии резервного копирования БД.
