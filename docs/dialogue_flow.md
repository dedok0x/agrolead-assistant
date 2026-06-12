# Диалоговый поток sales-ассистента (v7)

## Конвейер обработки сообщения

```
текст → guardrails → (подтверждение pending-факта?) → классификация сигнала
      → извлечение фактов → SalesDecision (score, missing, next_action)
      → DialoguePolicy (стратегия ответа) → [гейт лида] → CRM-синк
      → RAG → GigaChatComposer / template-policy → ResponseValidator → ответ
```

Бизнес-решения (тип заявки, следующий вопрос, статус) принимает **серверная
логика**, LLM только формулирует текст по переданному состоянию.

## Типы обращений

| v7-код | DB-код | Обязательные поля |
| --- | --- | --- |
| buy_grain | sale_to_buyer | request_type, product, volume, region, timing, contact |
| sell_grain | purchase_from_supplier | request_type, product, volume, region, timing, contact |
| logistics | logistics_request | request_type, product, volume, region (маршрут), timing, contact |
| storage | storage_request | request_type, product, volume, region, timing, contact |
| export | export_request | как buy/sell |
| consultation | general_company_request | request_type, contact |
| FAQ-вопрос | — (лид не создаётся) | — |

## Статусы фактов (slot filling)

- **missing** — поле не собрано, входит в `missing_fields`;
- **uncertain** — извлечено с низкой уверенностью (например, голое число «3000»
  в ответ на вопрос об объёме). Сохраняется в БД с `is_confirmed=false`,
  не закрывает missing field, бот переспрашивает «Правильно понял: …?»;
- **confirmed** — подтверждено явным значением или ответом «да/верно/ага»
  на переспрос. Ответ «нет/не так» удаляет pending-факт и возвращает вопрос.

## Правила выбора следующего вопроса

1. Один вопрос за реплику, по первому полю из `missing_fields`
   (порядок: request_type → product → volume → region → timing → contact).
2. Подтверждённые факты не переспрашиваются (защита и на уровне
   `ResponseValidator._asks_known_field` для LLM-ответов).
3. Если есть pending uncertain-факт — сначала подтверждение.
4. Все обязательные поля собраны → `handoff_manager`, статус `qualified`,
   резюме заявки для менеджера.

## Защита состояния

- Слабые маркеры («нужно», «требуется») не перетирают уже известный
  request_type; смена типа — только по явным глаголам («наоборот, хотим купить»).
- Мета-вопросы, smalltalk, негатив, консультация не пишут товарные факты
  (стратегии DialoguePolicy с `should_save_fact=false`).

## Создание лида

Лид в CRM создаётся только при коммерческом намерении: известен request_type
(кроме consultation) или собран хотя бы один из product/volume/region/contact.
FAQ-вопросы и smalltalk получают статус сессии `faq_only` без лида; при
появлении намерения в той же сессии лид создаётся обычным путём.

## Стадии лида

`draft` → `partially_qualified` (score ≥ 50) → `qualified` (score = 100)
→ `handed_to_manager` (вручную менеджером). Отдельно: `blocked` (guardrails),
`faq_only` (сессия без лида). Объём ≥ 1000 т включает `hot_flag` и приоритет high.

## Guardrails и валидация

- До LLM: пустой ввод (HTTP 400), security-паттерны, токсичность
  (hard/soft stop, `TOXIC_STRICT_MODE`); заблокированные сообщения не создают
  лидов и фактов.
- После LLM: `ResponseValidator` режет обещания цены/наличия/доставки без
  подтверждённого RAG-контекста, ограничивает длину, не даёт переспрашивать
  известные поля; fallback всегда продолжает сценарий, для FAQ — отвечает
  short_answer найденной статьи.
