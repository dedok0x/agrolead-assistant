from dataclasses import dataclass

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
            return AgentReply(text=text, provider=provider, model=model)
        except LLMUnavailableError:
            fallback = summary_lines[0] if summary_lines else "Заявку зафиксировал."
            if next_question:
                fallback = f"{fallback} {next_question}"
            return AgentReply(text=fallback, provider="service-unavailable", model="none")
