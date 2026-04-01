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
- `APP_PORT` — внешний порт;
- `DB_URL`, `MODEL_PROVIDER`, `MODEL_NAME`, `API_BASE`.

Для локальной LLM по умолчанию используется Ollama:

- `MODEL_PROVIDER=ollama`
- `MODEL_NAME=qwen2.5:7b`
- `API_BASE=http://ollama:11434/v1`
- `API_KEY` можно оставить пустым.

Для MVP используем SQLite:

- `DB_URL=sqlite:///data/picoclaw.db`
- данные сохраняются в volume `picoclaw_data`.

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
docker compose --env-file .env pull
docker compose --env-file .env up -d

# первый запуск локальной модели (один раз)
docker exec -it ollama ollama pull qwen2.5:7b
```

---

## 4) Проверка и тест

```bash
docker ps
docker logs -f picoclaw
curl http://<SERVER_IP>:<APP_PORT>/health
```

Если в контейнере другой health endpoint — замени путь `/health` на нужный.

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
