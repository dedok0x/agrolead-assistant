from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..domain.sales import NextAction
from ..llm_service import LLMService, LLMUnavailableError
from .response_validator import ResponseValidator


@dataclass(slots=True)
class ComposerResult:
    text: str
    provider: str
    model: str


class GigaChatComposer:
    def __init__(self, llm_service: LLMService, validator: ResponseValidator | None = None) -> None:
        self.llm_service = llm_service
        self.validator = validator or ResponseValidator()

    async def compose(
        self,
        *,
        user_message: str,
        source_channel: str,
        stage: str,
        next_action: str,
        known_facts: dict[str, Any],
        missing_fields: list[str],
        captured_fields: list[str],
        retrieved_context: list[Any],
        forbidden_claims: list[str] | None = None,
        dialogue_guidance: str | None = None,
        fallback_override: str | None = None,
    ) -> ComposerResult:
        fallback = fallback_override or self.validator.fallback(next_action, known_facts)
        system_prompt = (
            "Ты — деловой AI-ассистент компании «ПЕТРОХЛЕБ-КУБАНЬ». "
            "Ты помогаешь клиенту оформить заявку по зерну, логистике, хранению или ВЭД. "
            "Не выдумывай цены, наличие, сроки, условия доставки и контакты. "
            "Используй только переданный контекст. "
            "Твоя задача — сформулировать короткий профессиональный ответ и задать следующий нужный вопрос. "
            "Не задавай вопрос о поле, которое уже известно. "
            "Если next_action = handoff_manager, подтверди, что заявка собрана и будет передана менеджеру. "
            "Стиль: профессионально, коротко, понятно, без канцелярита."
        )
        payload = {
            "user_message": user_message,
            "source_channel": source_channel,
            "stage": stage,
            "next_action": next_action,
            "known_facts": known_facts,
            "missing_fields": missing_fields,
            "captured_fields": captured_fields,
            "retrieved_context": [item.to_dict() if hasattr(item, "to_dict") else item for item in retrieved_context],
            "dialogue_guidance": dialogue_guidance or "",
            "forbidden_claims": forbidden_claims or [
                "цены без контекста",
                "наличие без контекста",
                "сроки и условия доставки без контекста",
                "повторные вопросы по уже известным полям",
            ],
        }
        user_prompt = (
            "Сформулируй ответ на русском языке. Требования: 1-4 предложения, не более одного главного уточняющего вопроса, "
            "без markdown-таблиц в Telegram.\n"
            f"Данные backend:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        try:
            text, provider, model = await self.llm_service.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                reason=f"v7_{next_action}",
                temperature=0.35 if next_action != NextAction.ANSWER_FAQ.value else 0.45,
                max_tokens=280,
            )
        except LLMUnavailableError:
            return ComposerResult(text=fallback, provider="fallback", model="none")

        safe = self.validator.validate(
            text,
            next_action=next_action,
            known_facts=known_facts,
            retrieved_context=retrieved_context,
            source_channel=source_channel,
        )
        return ComposerResult(text=safe, provider=provider, model=model)
