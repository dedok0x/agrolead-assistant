# 03. Диалоговый движок, промпты и квалификация

## 1) Общая логика диалога

Диалогом управляет функция `_process_chat()` в `backend/app/main.py`.

Ключевой принцип: **сначала детерминированно собрать бизнес-факты, потом сгенерировать короткую человеческую формулировку через LLM**.

Это снижает риск "галлюцинаций": LLM не решает, что хранить в CRM, а только "как сказать".

## 2) Шаги `_process_chat` (детально)

1. Нормализация и валидация входного `text`.
2. Получение/создание `chat_session`.
3. Сохранение входящего сообщения (`chat_message`, `direction=in`).
4. Проверка guardrails:
   - если блок — формируется policy-ответ, и поток заканчивается;
   - если `stop_dialogue=True`, состояние сессии -> `blocked`.
5. Определение `request_type`:
   - сначала из текущего состояния сессии;
   - если пока общий тип, включается `detect_request_type(text)`.
6. Синхронизация lead-таблиц (`_sync_lead_tables`):
   - upsert в `crm_lead`;
   - upsert в `crm_lead_item`;
   - upsert в `crm_lead_contact_snapshot`;
   - привязка/создание `crm_counterparty` по контактам.
7. Извлечение фактов (`extract_facts`) + нормализация кодов в ID.
8. Upsert фактов в `chat_extracted_fact`.
9. Создание/обновление списка обязательных полей `chat_missing_field`.
10. Пересчет статуса лида (`draft` / `partially_qualified` / `qualified`).
11. Вычисление следующего вопроса (`next_question_for`).
12. Подготовка контекста:
    - RAG-линии из `knowledge_article`;
    - оффер-гипотеза;
    - стадия переговоров.
13. Вызов `agent.reply(...)`.
14. Пост-обработка ответа (`_clean_reply`) и сохранение `chat_message` (`direction=out`).

## 3) Request type detection

Определение типа запроса находится в `sales_logic.detect_request_type()`.

Используются:

- словари ключевых маркеров по типам;
- эвристика маршрута "из ... в ...";
- приоритет экспортной логики над общей логистикой;
- защита от ложных срабатываний на общий "нужна/нужно".

Важная особенность: при конфликте `purchase_from_supplier` и `sale_to_buyer` система может вернуть `general_company_request` и продолжить уточнения.

## 4) Извлечение фактов

`extract_facts()` извлекает:

- товар (`commodity_id`) по справочнику + алиасы;
- объем и единицу;
- контакт (телефон/email/telegram);
- имя/компанию;
- регион/маршрут;
- транспорт и базис поставки;
- экспортные признаки (страна/порт/export_flag);
- срочность.

Каждый факт хранится как `FactValue(text, numeric, confidence)`.

## 5) Обязательные поля по типам заявок

Источник: `REQUIRED_FIELDS_BY_REQUEST` в `sales_logic.py`.

- `purchase_from_supplier`:
  - `commodity_id`, `volume_value`, `volume_unit`, `source_region_id`, `contact_name_or_company`, `contact_phone_or_telegram_or_email`
- `sale_to_buyer`:
  - `commodity_id`, `requested_volume_value`, `requested_volume_unit`, `destination_region_id_or_port`, `contact_name_or_company`, `contact_phone_or_telegram_or_email`
- `logistics_request`:
  - `transport_mode_id`, `route_from`, `route_to`, `volume_value`, `cargo_type_text`, `contact_name_or_company`, `contact_phone_or_telegram_or_email`
- `storage_request`:
  - `location_text`, `volume_value`, `inbound_mode`, `outbound_mode_or_plan`, `storage_period_text`, `contact_name_or_company`, `contact_phone_or_telegram_or_email`
- `export_request`:
  - `commodity_id`, `volume_value`, `destination_country`, `port_text`, `contact_name_or_company`, `contact_phone_or_telegram_or_email`
- `general_company_request`:
  - `request_type_hint`, `contact_name_or_company`, `contact_phone_or_telegram_or_email`

## 6) Статусы квалификации

После извлечения фактов система пересчитывает состояние:

- `qualified`: все обязательные поля собраны с достаточной уверенностью.
- `partially_qualified`: есть минимально жизнеспособная заявка (`minimum_viable_application`) + контакт.
- `draft`: данных пока недостаточно.

Параллельно формируются:

- `chat_missing_field` — что еще нужно спросить;
- `chat_qualification_checkpoint` — аудит шагов квалификации.

## 7) Guardrails

`guardrails.evaluate_guardrails()` возвращает `GuardrailDecision`.

Режимы:

- `ok`: запрос безопасный.
- `empty_input`: пустой ввод.
- `security_block`: запросы про взлом/malware/ddos.
- `toxic_hard_stop`: жесткая токсичность, диалог останавливается.
- `toxic_soft_stop`: мягкая токсичность (в strict mode тоже стоп).

Ответы на блокировки берутся из `guardrail_response_policy.py`:

- несколько вариантов на decision code;
- детерминированная вариативность + защита от повторов последних ответов.

## 8) Промпт-конвейер

Промпты собираются в два слоя.

### Слой A: StagePromptBuilder (`backend/app/tools/response_tool.py`)

- `BASE_SYSTEM`: общие правила B2B-коммуникации.
- `STAGE_GUIDE`: подсказка по стадии (`new`, `draft`, `partially_qualified`, `qualified`, `faq`, `objection_handling`, ...).
- `user_prompt(...)` включает:
  - тип запроса;
  - стадия переговоров;
  - исходное сообщение клиента;
  - уже собранные факты;
  - RAG-контекст;
  - оффер-гипотезу;
  - последние ответы ассистента (anti-repeat);
  - следующий приоритетный вопрос.

### Слой B: LLMService (`backend/app/llm_service.py`)

Перед отправкой в GigaChat автоматически добавляются правила:

- отвечать только на русском;
- не выдумывать факты;
- не повторять формулировки.

Параметры ответа:

- температура зависит от стадии (`agent._temperature_for_stage`);
- лимит токенов в чате ~260;
- retries/timeout/parallelism управляются env.

## 9) Пост-обработка ответа LLM

`SalesAssistantAgent._clean_reply()`:

- убирает шаблонные префиксы типа "Понял запрос";
- предотвращает дословный повтор последних сообщений;
- если вопрос не прозвучал, добавляет `next_question`;
- при пустом тексте формирует fallback-ответ.

## 10) Где уже есть легаси-риски

- Настройки `intake.sequence.*` и `routing.rule.*` редактируются в админке, но runtime-логика backend их сейчас почти не использует.
- Эвристики extraction могут давать ложные срабатывания на коротких/шумных фразах.
- `asked_count` и `last_asked_at` в `chat_missing_field` не инкрементируются в текущем коде.
- Часть stage-логики разнесена между `status_code`, `current_state_code` и negotiation-stage, что усложняет поддержку.
