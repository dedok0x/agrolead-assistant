from dataclasses import dataclass
import re

from .llm_service import LLMService, LLMUnavailableError
from .tools.response_tool import StagePromptBuilder


@dataclass(slots=True)
class AgentReply:
    text: str
    provider: str
    model: str


class SalesAssistantAgent:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service
        self.prompt_builder = StagePromptBuilder()

    def _clean_reply(self, text: str, next_question: str, last_assistant_messages: list[str]) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return next_question or "Уточню детали и помогу оформить заявку."

        prefixes = [
            r"^понял[а]?\s+(твой|ваш)?\s*запрос[\s\.,!:-]*",
            r"^принял[а]?\s+(твой|ваш)?\s*запрос[\s\.,!:-]*",
            r"^спасибо[\s\.,!:-]*",
        ]
        for pattern in prefixes:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")

        if not cleaned:
            cleaned = next_question or "Уточню детали и помогу оформить заявку."

        if last_assistant_messages:
            for previous in last_assistant_messages[-2:]:
                prev = (previous or "").strip().lower()
                if prev and cleaned.lower() == prev:
                    cleaned = next_question or cleaned
                    break

        if next_question and next_question not in cleaned and "?" not in cleaned:
            cleaned = f"{cleaned}. {next_question}".strip()

        return cleaned

    async def reply(
        self,
        stage: str,
        request_type_name: str,
        user_text: str,
        summary_lines: list[str],
        next_question: str,
        last_assistant_messages: list[str],
    ) -> AgentReply:
        system_prompt = self.prompt_builder.system_prompt(stage)
        user_prompt = self.prompt_builder.user_prompt(
            user_text=user_text,
            request_type_name=request_type_name,
            summary_lines=summary_lines,
            next_question=next_question,
            last_replies=last_assistant_messages,
        )

        try:
            text, provider, model = await self.llm_service.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                reason=f"reply_{stage}",
                temperature=0.55,
                max_tokens=260,
            )
            safe_text = self._clean_reply(text, next_question=next_question, last_assistant_messages=last_assistant_messages)
            return AgentReply(text=safe_text, provider=provider, model=model)
        except LLMUnavailableError:
            fallback = summary_lines[0] if summary_lines else "Заявку зафиксировал."
            if next_question:
                fallback = f"{fallback} {next_question}"
            safe_fallback = self._clean_reply(fallback, next_question=next_question, last_assistant_messages=last_assistant_messages)
            return AgentReply(text=safe_fallback, provider="service-unavailable", model="none")
