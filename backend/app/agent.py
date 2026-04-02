from dataclasses import dataclass

from sqlmodel import Session

from .llm_service import LLMService, LLMUnavailableError
from .sales_logic import classify_intent, has_any_lead_data, mark_handoff
from .tools.lead_tool import LeadTool
from .tools.product_tool import ProductTool
from .tools.response_tool import StagePromptBuilder


@dataclass(slots=True)
class AgentResult:
    text: str
    provider: str
    model: str
    state: str
    reason: str


class SingleAgentOrchestrator:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service
        self.prompts = StagePromptBuilder()

    def _last_assistant_messages(self, history: str, limit: int = 3) -> list[str]:
        messages: list[str] = []
        for line in (history or "").splitlines():
            if line.startswith("Ассистент:"):
                value = line.replace("Ассистент:", "", 1).strip()
                if value:
                    messages.append(value)
        return messages[-limit:]

    def _pick_stage(self, state_name: str, has_data: bool, missing_fields: list[str], intent: str) -> str:
        if state_name == "handoff":
            return "post_handoff"
        if not has_data:
            return "greeting"
        if missing_fields:
            if intent == "product_lookup":
                return "product_lookup"
            if intent == "free_question":
                return "free_question"
            return "qualification"
        return "free_question"

    async def _generate_stage_reply(
        self,
        stage: str,
        text: str,
        history: str,
        lead_context: str,
        catalog_context: str,
        no_repeat_context: str,
        missing_fields: list[str],
    ) -> tuple[str, str, str]:
        rules = (
            "Правила ответа:\n"
            "1) Отвечай только на русском, естественно и по делу.\n"
            "2) Не используй шаблонные фразы и не копируй прошлые реплики.\n"
            "3) Не выдумывай факты, цены и остатки.\n"
        )

        if missing_fields:
            rules += (
                "4) Если данных по лиду не хватает, задай только один уточняющий вопрос в конце ответа. "
                "Вопрос должен соответствовать next_hint из контекста лида.\n"
            )
        else:
            rules += "4) Если данных достаточно, не задавай лишних уточнений.\n"

        prompt_parts = [
            rules,
            lead_context,
            catalog_context,
            no_repeat_context,
            f"История диалога:\n{history or '-'}",
            f"Сообщение клиента: {text}",
        ]
        user_prompt = "\n\n".join(part for part in prompt_parts if part)

        return await self.llm_service.complete(
            system_prompt=self.prompts.stage_system_prompt(stage),
            user_prompt=user_prompt,
            reason=f"stage_{stage}",
            temperature=0.62,
            max_tokens=320,
        )

    async def handle(
        self,
        session: Session,
        session_id: int,
        text: str,
        history: str,
        source_channel: str = "web",
    ) -> AgentResult:
        lead_tool = LeadTool(session)
        product_tool = ProductTool(session)

        state = lead_tool.update_state(session_id=session_id, text=text)
        missing_fields = lead_tool.missing_fields(state)
        has_data = has_any_lead_data(state)
        intent = classify_intent(text)

        items = product_tool.lookup(
            query=text,
            fallback_culture=state.product,
            fallback_grade=state.grade,
            limit=3,
        )
        next_hint = lead_tool.next_hint(state)
        lead_context = self.prompts.lead_context(state=state, missing_fields=missing_fields, next_hint=next_hint)
        catalog_context = self.prompts.catalog_context(items)
        no_repeat_context = self.prompts.no_repeat_context(self._last_assistant_messages(history))

        if lead_tool.is_complete(state) and state.state != "handoff":
            lead = lead_tool.save_qualified_lead(session_id=session_id, state=state, source_channel=source_channel)
            crm_status = lead_tool.crm_stub(lead)
            handoff_prompt = self.prompts.handoff_brief(lead=lead, crm_reference=crm_status["crm_reference"])

            try:
                text_out, provider, model = await self.llm_service.complete(
                    system_prompt=self.prompts.stage_system_prompt("handoff"),
                    user_prompt="\n\n".join(
                        part
                        for part in [handoff_prompt, no_repeat_context, f"История диалога:\n{history or '-'}"]
                        if part
                    ),
                    reason="stage_handoff",
                    temperature=0.48,
                    max_tokens=260,
                )
            except LLMUnavailableError:
                provider = "service-unavailable"
                model = "none"
                text_out = "Заявку зафиксировал и передал менеджеру. Он свяжется с вами по указанному контакту."

            state = mark_handoff(session=session, state=state, provider=provider, model=model)
            return AgentResult(
                text=text_out,
                provider=provider,
                model=model,
                state=state.state,
                reason="lead_qualified",
            )

        stage = self._pick_stage(
            state_name=state.state,
            has_data=has_data,
            missing_fields=missing_fields,
            intent=intent,
        )

        try:
            text_out, provider, model = await self._generate_stage_reply(
                stage=stage,
                text=text,
                history=history,
                lead_context=lead_context,
                catalog_context=catalog_context,
                no_repeat_context=no_repeat_context,
                missing_fields=missing_fields,
            )
        except LLMUnavailableError:
            return AgentResult(
                text="Сейчас сервис ответа недоступен. Продолжим сделку: напишите, пожалуйста, товар и объем.",
                provider="service-unavailable",
                model="none",
                state=state.state,
                reason=f"stage_{stage}_unavailable",
            )

        return AgentResult(
            text=text_out,
            provider=provider,
            model=model,
            state=state.state,
            reason=f"stage_{stage}",
        )
