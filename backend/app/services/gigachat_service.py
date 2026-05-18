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
        is_agentic_dialogue = bool(dialogue_guidance)
        effective_next_action = NextAction.ANSWER_FAQ.value if is_agentic_dialogue else next_action
        system_prompt = (
            "Ты деловой AI-ассистент компании «ПЕТРОХЛЕБ-КУБАНЬ». "
            "Компания работает с B2B-заявками по продаже и покупке зерна, логистике, хранению и ВЭД. "
            "Твоя задача — вести разговор как живой предпродажный ассистент: сначала понять человека, затем мягко собрать параметры сделки, если это уместно. "
            "Не выдумывай цены, наличие, сроки, условия доставки и контакты. Используй только переданный контекст и состояние backend. "
            "Если пользователь спрашивает, кто ты, просит просто консультацию или критикует качество диалога, ответь по-человечески и не превращай ответ в анкету. "
            "Если есть dialogue_guidance, следуй ему как главному сценарию ответа. "
            "Ответ: 1-4 предложения, без markdown-таблиц, не более одного естественного вопроса."
        )
        payload = {
            "user_message": user_message,
            "source_channel": source_channel,
            "stage": stage,
            "next_action": effective_next_action,
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
            "Сформулируй ответ на русском языке по данным backend. "
            "Не добавляй факты, которых нет в known_facts или retrieved_context. "
            "Если dialogue_guidance заполнен, возьми его смысл за основу и сделай ответ естественным.\n"
            f"Данные backend:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        try:
            text, provider, model = await self.llm_service.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                reason=f"v7_{effective_next_action}",
                temperature=0.55 if is_agentic_dialogue else 0.35,
                max_tokens=320 if is_agentic_dialogue else 280,
            )
        except LLMUnavailableError:
            return ComposerResult(text=fallback, provider="fallback", model="none")

        safe = self.validator.validate(
            text,
            next_action=effective_next_action,
            known_facts=known_facts,
            retrieved_context=retrieved_context,
            source_channel=source_channel,
        )
        return ComposerResult(text=safe, provider=provider, model=model)
