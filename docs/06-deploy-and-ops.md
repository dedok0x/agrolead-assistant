# 06. Деплой и эксплуатация

## 1) Стек деплоя

- Оркестрация: Docker Compose (`docker-compose.yml`).
- Сервисы:
  - `db` (PostgreSQL 16-alpine)
  - `api` (FastAPI + Uvicorn)
  - `webui` (Nginx + статические страницы + `/api/` proxy, только HTTP на `127.0.0.1:8080`)

## Границы ответственности

- TLS, порты 80/443, сертификаты и ACME для **всех** доменов сервера
  обслуживает хостовый nginx (`/etc/nginx/sites-available/*.conf`) и хостовый
  certbot (`certbot.timer`). Этот проект ими не управляет.
- Этот репозиторий описывает только `artemshtodin.ru`-приложение
  (db/api/webui). Конфиги `codex.*`, `web.*`, `zabbix.*`, Kasm, code-server,
  Telegram-агентов сюда добавлять нельзя.

## 2) Требования к окружению

- Docker + Docker Compose plugin.
- `curl`.
- `python3` или `python` (используется в deploy script).
- Домен из `DOMAIN_NAME` должен указывать на сервер.
- Хостовый nginx должен проксировать `artemshtodin.ru` на `127.0.0.1:8080`.

## 3) Переменные окружения

Источник шаблона: `.env.example`.

Критичные:

- DB: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`.
- Admin: `ADMIN_USER`, `ADMIN_PASS`, `ALLOW_STATIC_ADMIN_TOKEN`, `ADMIN_SESSION_TTL_MINUTES`.
- Домен: `DOMAIN_NAME` (используется только для внешнего smoke-чека).
- LLM/GigaChat: `LLM_PROVIDER`, `GIGACHAT_AUTH_KEY`, `GIGACHAT_AUTH_URL`, `GIGACHAT_API_BASE_URL`, `GIGACHAT_MODEL`.

`POSTGRES_PASSWORD` и `ADMIN_PASS` не должны храниться в git. Если они пустые при запуске `deploy.sh`, скрипт сгенерирует значения в локальном `.env`.

## 4) Стандартный порядок деплоя (`deploy.sh`)

Скрипт `deploy.sh` выполняет полный сценарий:

1. Проверяет окружение и нужные команды.
2. Готовит `.env` и дописывает отсутствующие обязательные значения.
3. Останавливает старый стек (`docker compose down --remove-orphans`).
4. Пересобирает образы (`build --no-cache --pull`).
5. Поднимает контейнеры (`up -d --force-recreate`).
6. Выполняет health check API и smoke-тесты:
   - admin login
   - supplier/buyer/faq chat
   - проверка лидов
   - проверка каталогов и admin endpoint'ов
   - проверка webui (`127.0.0.1:8080`) и assets
   - нефатальная проверка `https://$DOMAIN_NAME` через хостовый nginx
   - проверка runtime-кода в контейнере
   - запуск unit/integration тестов backend
7. Печатает итоговые URL и путь к логу деплоя.

## 5) Ручной запуск без deploy.sh

```bash
cp .env.example .env
# заполнить POSTGRES_PASSWORD, DATABASE_URL, ADMIN_PASS
docker compose build
docker compose up -d
```

Проверки после запуска:

- `GET http://localhost:8000/api/health`
- `https://artemshtodin.ru`
- `https://artemshtodin.ru/admin`

## 6) Эксплуатационные URL

- Чат: `https://artemshtodin.ru`
- Админка: `https://artemshtodin.ru/admin`
- API docs: `http://localhost:8000/docs` на сервере
- Health: `http://localhost:8000/api/health` на сервере

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

HTTPS обслуживает хостовый nginx, не этот проект:

- `nginx -t && systemctl status nginx` на хосте;
- `certbot certificates` на хосте;
- проверить, что `agrolead-webui` слушает `127.0.0.1:8080` (`curl -I http://127.0.0.1:8080`);
- убедиться, что DNS `DOMAIN_NAME` указывает на сервер.

## 8) Важные скрипты

- `deploy/deploy.sh`
  - thin-wrapper, который вызывает root `deploy.sh`.
- `deploy/wipe_project.sh`
  - деструктивный скрипт: удаляет контейнеры, volume, образы и саму директорию проекта.
  - запуск только с флагом `--yes`.

## 9) Минимальный runbook для продакшен-стенда

1. Перед релизом проверить `.env` и DNS.
2. Запустить `bash ./deploy.sh`.
3. Проверить smoke-кейсы и `api/health`.
4. Проверить вход в админку и создание тестовой заявки.
5. Проверить `curl -I http://artemshtodin.ru` и `curl -I https://artemshtodin.ru`.
6. Зафиксировать версию образов и лог деплоя.

## 10) Операционные риски текущей версии

- Deploy всегда rebuild с `--no-cache` (долго и затратно).
- Нет blue/green/canary механики.
- Нет централизованных метрик/алертов.
- Нет встроенной стратегии резервного копирования БД.
