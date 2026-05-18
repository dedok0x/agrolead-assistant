from __future__ import annotations

import re
from typing import Any

from ..domain.sales import NextAction


FALLBACKS = {
    NextAction.ASK_REQUEST_TYPE.value: "Понял. Уточните, пожалуйста, тип заявки: продажа зерна, покупка, логистика, хранение, ВЭД или консультация?",
    NextAction.ASK_PRODUCT.value: "Понял. Какую культуру или груз фиксируем в заявке?",
    NextAction.ASK_VOLUME.value: "Хорошо. Подскажите, пожалуйста, какой объем рассматриваете?",
    NextAction.ASK_REGION.value: "Принял. Уточните, пожалуйста, регион или маршрут поставки.",
    NextAction.ASK_TIMING.value: "Понял. В какие сроки нужна сделка или поставка?",
    NextAction.ASK_CONTACT.value: "Остался контакт для связи. Укажите, пожалуйста, телефон, Telegram или email.",
    NextAction.HANDOFF_MANAGER.value: "Заявка собрана. Передаю ее менеджеру для дальнейшей обработки.",
    NextAction.HANDLE_OBJECTION.value: "Понимаю, что важно сначала оценить условия. Чтобы менеджер дал предметный ориентир, уточните недостающие параметры заявки.",
    NextAction.ANSWER_FAQ.value: "Отвечу по справочникам компании и продолжу сбор заявки. Уточните, пожалуйста, следующий недостающий параметр.",
    NextAction.REFUSE_IRRELEVANT.value: "Я помогаю по заявкам на зерно, логистику, хранение и ВЭД. Уточните задачу в этих направлениях.",
}


FIELD_QUESTIONS = {
    "request_type": ["тип заявки", "продажа", "покупка", "логистика", "хранение"],
    "product": ["какую культуру", "какой груз", "культуру"],
    "volume": ["какой объем", "объем"],
    "region": ["какой регион", "регион", "маршрут"],
    "timing": ["какие сроки", "в какие сроки", "срок"],
    "contact": ["контакт", "телефон", "email", "telegram"],
}


class ResponseValidator:
    def validate(
        self,
        text: str,
        *,
        next_action: str,
        known_facts: dict[str, Any],
        retrieved_context: list[Any] | None,
        source_channel: str = "web_widget",
    ) -> str:
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        context = retrieved_context or []
        if not cleaned:
            return self.fallback(next_action, known_facts)

        if self._mentions_price(cleaned) and not any(getattr(item, "has_price", False) for item in context):
            return self.fallback(next_action, known_facts)
        if self._mentions_availability(cleaned) and not any(getattr(item, "has_availability", False) for item in context):
            return self.fallback(next_action, known_facts)
        if self._promises_delivery(cleaned) and not any(getattr(item, "has_delivery_terms", False) for item in context):
            return self.fallback(next_action, known_facts)
        if self._too_long(cleaned, source_channel):
            return self.fallback(next_action, known_facts)
        if self._asks_known_field(cleaned, known_facts):
            return self.fallback(next_action, known_facts)
        if self._action_requires_question(next_action) and "?" not in cleaned:
            return f"{cleaned} {self.fallback(next_action, known_facts)}"
        if source_channel == "telegram":
            cleaned = cleaned.replace("|", " ")
        return cleaned

    def fallback(self, next_action: str, known_facts: dict[str, Any] | None = None) -> str:
        if next_action == NextAction.HANDLE_OBJECTION.value:
            return self._objection_fallback(known_facts or {})
        if next_action == NextAction.ANSWER_FAQ.value:
            return self._faq_fallback(known_facts or {})
        return FALLBACKS.get(next_action, FALLBACKS[NextAction.ASK_REQUEST_TYPE.value])

    @staticmethod
    def _mentions_price(text: str) -> bool:
        lowered = text.lower()
        return bool(re.search(r"\b\d+[,.]?\d*\s*(?:руб|₽|р/т|руб/т)", lowered)) or any(marker in lowered for marker in ["цена ", "стоимость"])

    @staticmethod
    def _mentions_availability(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in ["есть в наличии", "имеется в наличии", "доступно к отгрузке"])

    @staticmethod
    def _promises_delivery(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in ["доставим", "гарантируем доставку", "срок доставки", "привезем за"])

    @staticmethod
    def _too_long(text: str, source_channel: str) -> bool:
        limit = 560 if source_channel == "telegram" else 760
        return len(text) > limit or len(re.split(r"[.!?]+", text)) > 6

    @staticmethod
    def _action_requires_question(next_action: str) -> bool:
        return next_action in {
            NextAction.ASK_REQUEST_TYPE.value,
            NextAction.ASK_PRODUCT.value,
            NextAction.ASK_VOLUME.value,
            NextAction.ASK_REGION.value,
            NextAction.ASK_TIMING.value,
            NextAction.ASK_CONTACT.value,
        }

    @staticmethod
    def _asks_known_field(text: str, known_facts: dict[str, Any]) -> bool:
        lowered = text.lower()
        for field, markers in FIELD_QUESTIONS.items():
            if not known_facts.get(field):
                continue
            if "?" in lowered and any(marker in lowered for marker in markers):
                return True
        return False

    def _objection_fallback(self, known_facts: dict[str, Any]) -> str:
        for field, question in [
            ("product", FALLBACKS[NextAction.ASK_PRODUCT.value]),
            ("volume", FALLBACKS[NextAction.ASK_VOLUME.value]),
            ("region", FALLBACKS[NextAction.ASK_REGION.value]),
            ("timing", FALLBACKS[NextAction.ASK_TIMING.value]),
            ("contact", FALLBACKS[NextAction.ASK_CONTACT.value]),
        ]:
            if not known_facts.get(field):
                return f"Понимаю, условия важны. Для точного ответа менеджеру нужен контекст: {question}"
        return FALLBACKS[NextAction.HANDOFF_MANAGER.value]

    def _faq_fallback(self, known_facts: dict[str, Any]) -> str:
        for field, action in [
            ("request_type", NextAction.ASK_REQUEST_TYPE.value),
            ("product", NextAction.ASK_PRODUCT.value),
            ("volume", NextAction.ASK_VOLUME.value),
            ("region", NextAction.ASK_REGION.value),
            ("timing", NextAction.ASK_TIMING.value),
            ("contact", NextAction.ASK_CONTACT.value),
        ]:
            if not known_facts.get(field):
                return f"По справочникам компании отвечу коротко и продолжу сбор заявки. {FALLBACKS[action]}"
        return FALLBACKS[NextAction.HANDOFF_MANAGER.value]
