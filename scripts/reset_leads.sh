#!/usr/bin/env bash
# Очистка транзакционных данных заявок/чатов. Справочники, каталоги, knowledge
# и админ-данные НЕ трогаем — они нужны для работы ассистента.
#
# Использование:
#   ./scripts/reset_leads.sh            # через docker-контейнер agrolead-db
#   DB_CONTAINER=agrolead-db DB_USER=agrolead DB_NAME=agrolead ./scripts/reset_leads.sh
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-agrolead-db}"
DB_USER="${DB_USER:-agrolead}"
DB_NAME="${DB_NAME:-agrolead}"

TABLES="crm_lead_document_request crm_task crm_lead_contact_snapshot crm_lead_item crm_lead \
chat_qualification_checkpoint chat_missing_field chat_extracted_fact chat_message chat_session"

# Берём только реально существующие таблицы (схема может отличаться между версиями).
EXISTING=""
for t in $TABLES; do
  found=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT to_regclass('public.$t') IS NOT NULL;" | tr -d '[:space:]')
  if [ "$found" = "t" ]; then
    EXISTING="$EXISTING $t"
  fi
done

if [ -z "$EXISTING" ]; then
  echo "Не найдено транзакционных таблиц для очистки."
  exit 0
fi

# shellcheck disable=SC2086
TRUNCATE_LIST=$(echo $EXISTING | tr ' ' ',')
echo "Очищаю: $TRUNCATE_LIST"
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c \
  "TRUNCATE TABLE $TRUNCATE_LIST RESTART IDENTITY CASCADE;"

echo "Готово. Текущие счётчики:"
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT 'crm_lead='||count(*) FROM crm_lead; SELECT 'chat_session='||count(*) FROM chat_session; SELECT 'knowledge_article='||count(*) FROM knowledge_article; SELECT 'ref_commodity='||count(*) FROM ref_commodity;"
