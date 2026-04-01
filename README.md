# AgroLead Assistant

AI-прототип ИС для автоматизации предпродажной работы в зерновом бизнесе.

Функции MVP:

- обработка первичных обращений клиентов;
- ответы по наличию и цене;
- квалификация лидов;
- создание заявки и передача менеджеру.

---

## 1) Что в репозитории

- `docker-compose.yml` — базовый контур запуска PicoClaw.
- `.env.example` — шаблон переменных окружения.
- `deploy/install_picoclaw.sh` — автоматическая установка Docker + Compose на Ubuntu и запуск сервиса.
- `config/picoclaw-config.json` — фиксированная конфигурация PicoClaw для локальной LLM (Ollama).
- `web/index.html` — веб-морда для теста gateway (минималистичный стиль).
- `web/nginx.conf` — nginx proxy до PicoClaw API.
- `ssl/README.md` — требования к SSL-файлам для HTTPS (443).
- `docs/architecture.md` — архитектурный каркас для ВКР.
- `docs/scenarios.md` — минимальные сценарии MVP.
- `docs/test-report.md` — шаблон протокола тестирования.

---

## 2) Быстрый запуск на Ubuntu

### Шаг 1. Подготовь переменные

```bash
cp .env.example .env
```

Заполни в `.env`:

- `PICOCLAW_IMAGE` — Docker-образ;
- `APP_PORT` — внешний порт (по умолчанию 18790 для gateway);
- `DB_URL`, `MODEL_PROVIDER`, `MODEL_NAME`, `API_BASE`.

Для локальной LLM по умолчанию используется Ollama:

- `MODEL_PROVIDER=ollama`
- `MODEL_NAME=qwen2.5:0.5b`
- `API_BASE=http://127.0.0.1:11434/v1`
- `API_KEY` можно оставить пустым.

Для MVP используем SQLite:

- `DB_URL=sqlite:///data/picoclaw.db`
- данные сохраняются в volume `picoclaw_data`.

Конфиг PicoClaw берётся из файла `config/picoclaw-config.json` (монтируется в контейнер как `/root/.picoclaw/config.json`).

### Шаг 2. Запусти установку и деплой

```bash
chmod +x deploy/install_picoclaw.sh
./deploy/install_picoclaw.sh
```

Скрипт:

1. Установит Docker Engine и Docker Compose plugin.
2. Включит автозапуск Docker.
3. Поднимет контейнер PicoClaw с политикой `restart: unless-stopped`.

---

## 3) Ручной запуск (без скрипта)

```bash
docker compose pull
docker compose up -d

# первый запуск локальной модели (один раз)
docker exec -it ollama ollama pull qwen2.5:0.5b
```

`docker compose` автоматически читает файл `.env` из текущей директории.

---

## 4) Проверка и тест

```bash
docker ps
docker logs -f picoclaw
curl http://<SERVER_IP>:<APP_PORT>/health
```

### Веб-тест через UI (HTTPS)

После `docker compose up -d` открой:

- `https://<SERVER_IP>`
- `https://<DOMAIN>`

Это тестовая веб-морда в стиле минималистичного сайта:

- кнопка `Проверить health` бьёт в `/api/health`;
- кнопка `Отправить тестовый запрос` по умолчанию шлёт запрос в Ollama через `/llm/api/generate`.

Текущая версия UI работает как лид-чат поддержки: без ручных тех.параметров на главной странице, с потоковой выдачей ответа и отраслевым ограничением тематики (зерновая продукция и оформление заявки).

Перед запуском HTTPS положи файлы в папку `ssl/`:

- `ssl/fullchain.pem` — цепочка (leaf + intermediate)
- `ssl/privkey.key` — приватный ключ

Если в контейнере другой health endpoint — замени путь `/health` на нужный.

Важно: образ запускает gateway через entrypoint. В compose передаётся только флаг `--allow-empty`, поэтому сервис не должен постоянно перезапускаться.

PicoClaw и Ollama работают в `network_mode: host`, а веб-морда проксирует API на `host.docker.internal:18790`.

Проверка автозапуска:

```bash
sudo reboot
# после загрузки
docker ps
```

---

## 5) Как развивать диплом после MVP

1. При росте нагрузки перейти с SQLite на PostgreSQL и Redis в `docker-compose.yml`.
2. Ввести миграции схемы данных.
3. Добавить структурированное логирование и метрики.
4. Провести регрессионные прогоны сценариев A/B/C/D.
5. Оформить диаграммы и результаты тестов в `docs/`.

---

## 6) Полезные документы в этом репозитории

- Архитектура: `docs/architecture.md`
- Сценарии MVP: `docs/scenarios.md`
- Протокол тестов: `docs/test-report.md`
