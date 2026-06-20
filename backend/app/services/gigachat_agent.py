"""GigaChat-центричный ход диалога.

Один структурированный вызов GigaChat за ход: модель сама извлекает факты из
сообщения (без регулярок), решает тип запроса и интент, оценивает квалификацию
и формулирует живую реплику. На вход подаются только нужные данные —
накопленные факты, обязательные поля, RAG-контекст (инструкции, номенклатура,
справочники) и последние реплики ассистента для анти-повтора.

Извлечение и формулировка — на стороне ИИ; детерминированная перепроверка
квалификации делается в вызывающем коде (cross-check по required-fields).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..domain.sales import REQUEST_TYPE_LABELS, RequestType
from ..llm_service import LLMService

# Ключи фактов, которые модель имеет право заполнять. Совпадают со схемой,
# которую понимает CRM-синхронизация и качалка квалификации.
FACT_KEYS = [
    "request_type",
    "product",
    "volume",
    "region",
    "timing",
    "contact",
    "quality_class",
    "delivery_terms",
    "company_name",
    "route_from",
    "route_to",
    "transport_type",
    "comment",
]

VALID_REQUEST_TYPES = {item.value for item in RequestType}
VALID_INTENTS = {"commercial", "smalltalk", "meta", "irrelevant", "faq"}


@dataclass(slots=True)
class AgentTurn:
    intent: str = "commercial"
    is_service_message: bool = False
    request_type: str | None = None
    updated_facts: dict[str, Any] = field(default_factory=dict)
    newly_extracted: list[str] = field(default_factory=list)
    reply_text: str = ""
    provider: str = "gigachat"
    model: str = "none"


class GigaChatAgent:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    async def run_turn(
        self,
        *,
        user_message: str,
        known_facts: dict[str, Any],
        required_fields: list[str],
        retrieved_context: list[Any],
        last_assistant_messages: list[str],
        source_channel: str = "web_widget",
    ) -> AgentTurn:
        system_prompt = self._system_prompt()
        payload = {
            "user_message": user_message,
            "source_channel": source_channel,
            "known_facts": known_facts,
            "required_fields": required_fields,
            "last_assistant_messages": last_assistant_messages[-3:],
            "reference_context": [
                item.to_dict() if hasattr(item, "to_dict") else item for item in retrieved_context
            ],
        }
        user_prompt = (
            "Обработай сообщение клиента по данным backend и верни JSON по схеме из системной инструкции. "
            "Извлекай факты только из самого сообщения и known_facts, не выдумывай.\n"
            f"Данные backend:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        data, provider, model = await self.llm_service.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            reason="agent_turn",
            temperature=0.2,
            max_tokens=700,
        )
        return self._normalize(data, known_facts, provider, model)

    def _system_prompt(self) -> str:
        request_type_lines = "\n".join(
            f"  - {value}: {REQUEST_TYPE_LABELS[value]}" for value in REQUEST_TYPE_LABELS
        )
        return (
            "Ты — деловой AI-ассистент компании «ПЕТРОХЛЕБ-КУБАНЬ» (B2B-трейдер и логистический "
            "оператор по зерновым и масличным). Ты ведёшь живой предпродажный диалог на русском языке: "
            "сначала понимаешь человека, затем мягко собираешь параметры сделки.\n"
            "Правила:\n"
            "- Не выдумывай цены, наличие, остатки, сроки, условия доставки и контакты. "
            "Опирайся только на reference_context, known_facts и сообщение клиента.\n"
            "- За один ход задавай максимум один естественный вопрос, без анкетной сухости и без markdown-таблиц.\n"
            "- Если клиент здоровается, просто болтает, спрашивает «кто ты» или критикует диалог — "
            "ответь по-человечески и не превращай ответ в анкету (intent=smalltalk/meta, is_service_message=true).\n"
            "- Если вопрос справочный (что за компания, какие культуры, как идёт сделка) — ответь по reference_context (intent=faq).\n"
            "- Если запрос не по теме зерна/логистики/хранения/ВЭД — вежливо верни в тему (intent=irrelevant, is_service_message=true).\n"
            "- Извлекай факты из сообщения: культура, объём (в тоннах), регион/маршрут, сроки, контакт, "
            "качество/класс, условия поставки, компания. Учитывай поправки клиента: если он говорит «не просо, а пшеница» — "
            "поправь product. Если отказывается от значения — верни это поле пустой строкой.\n"
            f"- request_type выбирай строго из:\n{request_type_lines}\n"
            "Верни JSON со строго такими ключами:\n"
            "{\n"
            '  "intent": "commercial|smalltalk|meta|irrelevant|faq",\n'
            '  "is_service_message": true|false,\n'
            '  "request_type": "<один из кодов выше или null>",\n'
            '  "updated_facts": {"request_type": "...", "product": "...", "volume": "1000 тонн", "region": "...", '
            '"timing": "...", "contact": "...", "quality_class": "...", "delivery_terms": "...", '
            '"company_name": "...", "route_from": "...", "route_to": "...", "transport_type": "...", "comment": "..."},\n'
            '  "newly_extracted": ["product", "volume"],\n'
            '  "reply_text": "<живой ответ клиенту на русском, без служебных данных>"\n'
            "}\n"
            "updated_facts — это ПОЛНОЕ накопленное состояние (старые известные факты плюс новые из этого сообщения). "
            "Не включай поля, по которым нет данных. volume пиши с единицей («1000 тонн»)."
        )

    def _normalize(
        self,
        data: dict[str, Any],
        known_facts: dict[str, Any],
        provider: str,
        model: str,
    ) -> AgentTurn:
        intent = str(data.get("intent") or "commercial").strip().lower()
        if intent not in VALID_INTENTS:
            intent = "commercial"

        request_type = data.get("request_type")
        if isinstance(request_type, str):
            request_type = request_type.strip() or None
        if request_type not in VALID_REQUEST_TYPES:
            request_type = None

        raw_facts = data.get("updated_facts")
        updated_facts: dict[str, Any] = {}
        if isinstance(raw_facts, dict):
            for key in FACT_KEYS:
                if key not in raw_facts:
                    continue
                value = raw_facts[key]
                if value is None:
                    updated_facts[key] = ""
                    continue
                if isinstance(value, (int, float)):
                    value = str(value)
                if isinstance(value, str):
                    updated_facts[key] = value.strip()
        if request_type and not updated_facts.get("request_type"):
            updated_facts["request_type"] = request_type
        if updated_facts.get("request_type") in VALID_REQUEST_TYPES:
            request_type = updated_facts["request_type"]

        newly = data.get("newly_extracted")
        newly_extracted = [str(item) for item in newly if str(item) in FACT_KEYS] if isinstance(newly, list) else []

        is_service = bool(data.get("is_service_message", False))
        if intent in {"smalltalk", "meta", "irrelevant"}:
            is_service = True

        reply_text = str(data.get("reply_text") or "").strip()

        return AgentTurn(
            intent=intent,
            is_service_message=is_service,
            request_type=request_type,
            updated_facts=updated_facts,
            newly_extracted=newly_extracted,
            reply_text=reply_text,
            provider=provider,
            model=model,
        )
