#!/usr/bin/env bash
set -Eeuo pipefail

# Полная очистка проекта agrolead-assistant:
# - останавливает и удаляет связанные контейнеры/сети/тома/образы
# - удаляет локальные docker-артефакты проекта
# - удаляет папку репозитория
# - возвращает в домашнюю директорию

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_NAME="$(basename "$REPO_DIR")"

if [[ "${1:-}" != "--yes" ]]; then
  echo "Этот скрипт УДАЛИТ проект '$REPO_NAME' и связанные Docker-артефакты."
  echo "Запуск: bash deploy/wipe_project.sh --yes"
  exit 1
fi

echo "[1/7] Остановка и удаление compose-стека"
if command -v docker >/dev/null 2>&1; then
  if [[ -f "$REPO_DIR/docker-compose.yml" ]]; then
    docker compose -f "$REPO_DIR/docker-compose.yml" down -v --rmi all --remove-orphans || true
  fi

  echo "[2/7] Удаление контейнеров проекта"
  for c in agrolead-db agrolead-api agrolead-webui agrolead-ollama agrolead-ollama-init agrolead-nanoclaw-agent; do
    docker rm -f "$c" >/dev/null 2>&1 || true
  done

  echo "[3/7] Удаление volumes проекта"
  while IFS= read -r vol; do
    [[ -n "$vol" ]] && docker volume rm -f "$vol" >/dev/null 2>&1 || true
  done < <(
    (
      docker volume ls -q --filter "name=agrolead-assistant" || true
      docker volume ls -q --filter "name=agrolead" || true
      docker volume ls -q --filter "name=nanoclaw" || true
      docker volume ls -q --filter "name=ollama" || true
    ) | sort -u
  )

  echo "[4/7] Удаление образов проекта"
  while IFS= read -r img; do
    [[ -n "$img" ]] && docker image rm -f "$img" >/dev/null 2>&1 || true
  done < <(
    docker image ls --format "{{.Repository}}:{{.Tag}}" | while IFS= read -r row; do
      case "$row" in
        agrolead/*|agrolead-assistant*|*agrolead*api*|*agrolead*webui*|*nanoclaw-agent*)
          echo "$row"
          ;;
      esac
    done
  )

  echo "[5/7] Очистка build cache"
  docker builder prune -af >/dev/null 2>&1 || true
else
  echo "Docker не найден, docker-очистка пропущена"
fi

echo "[6/7] Переход в HOME"
cd "$HOME"

echo "[7/7] Удаление директории репозитория"
rm -rf "$REPO_DIR"

echo "Готово: проект и связанные артефакты удалены. Текущая директория: $PWD"
