# Отчёт о доработке sales-ассистента (июнь 2026)

## Что было плохо
- «купить … с доставкой» классифицировалось как логистика (маркеры логистики проверялись раньше глаголов сделки);
- слабые маркеры («нужно») перетирали подтверждённый тип заявки;
- uncertain-факты не сохранялись между репликами, «да» на переспрос ничего не подтверждал;
- FAQ-вопросы («Какие документы нужны…») превращались в анкету;
- любое сообщение (smalltalk, «кто ты») создавало пустой лид в CRM;
- «3 класса», «100 тонны», диапазоны объёма не извлекались; телефон утекал в timing; маршрут «из X в Y» захватывал хвост фразы и не резолвился в регионы CRM;
- логистика квалифицировалась без груза и объёма;
- NDJSON-финал не содержал `text`; в main.py висело ~230 строк мёртвого v6-кода.

## Что изменено
- `services/field_extractor.py` — детектор типа заявки, объём (формы слова, тыс., диапазоны), качество, timing-фразы, маршрут (route_from/route_to), company_name, защита от телефона в дате;
- `services/conversation_service.py` — персист uncertain-фактов, подтверждение «да»/отклонение «нет», фильтрация known по is_confirmed/confidence, гейт создания лида (faq_only без лида), разбор маршрута в source/destination region CRM;
- `services/user_signal_service.py`, `domain/sales.py`, `services/sales_engine.py`, `services/dialogue_policy.py` — сигнал FAQ_QUESTION → answer_faq + ответ по RAG (fallback из short_answer статьи);
- `services/lead_qualification_service.py` — логистика требует product и volume;
- `main.py` — удалён мёртвый v6-код, `text` добавлен в финальное NDJSON-событие.

## Типы заявок
Покупка, продажа, логистика, хранение, экспорт, консультация, FAQ (без лида), нерелевантное (отказ), токсичное (блок) — покрыты тестами.

## Тесты
44 → **79 passed**. Новые: test_dialogue_intelligence.py, test_lead_types.py, test_sales_assistant_flow.py, test_api_contract_consistency.py.

## Smoke
`API_BASE_URL=http://127.0.0.1:8000 ./scripts/smoke_dialogue.sh` — 7 сценариев, все OK против живого API (см. docs/testing.md).

## Осталось на будущее
- legacy v6 (`sales_logic.py`, `agent.py`, dry-run) не удалён — используется только `/api/chat/dry-run`;
- словари регионов/культур ограничены; стоит вынести в БД-справочники;
- e2e с реальным GigaChat не прогонялся (в тестах мок/выкл);
- `on_event` deprecated — мигрировать на lifespan.
