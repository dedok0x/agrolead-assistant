from dataclasses import dataclass

from sqlmodel import Session

from .llm_service import LLMService, LLMUnavailableError
from .sales_logic import classify_intent, has_any_lead_data, mark_handoff
from .tools.lead_tool import LeadTool
from .tools.product_tool import ProductTool
from .tools.response_tool import ResponseRenderer


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
        self.renderer = ResponseRenderer()

    async def _answer_free_question(self, text: str, catalog_hint: str, history: str) -> tuple[str, str, str]:
        prompt = (
            "Клиент задал свободный вопрос по зерновой сделке. "
            "Ответь коротко, реалистично и только на русском. "
            "Если не хватает данных, запроси уточнение.\n\n"
            f"История:\n{history}\n\n"
            f"Каталог-подсказка:\n{catalog_hint}\n\n"
            f"Вопрос: {text}"
        )
        return await self.llm_service.complete(
            system_prompt="Ты продающий ассистент по зерновым B2B сделкам.",
            user_prompt=prompt,
            reason="free_question",
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
        missing = lead_tool.missing_fields(state)
        has_data = has_any_lead_data(state)
        intent = classify_intent(text)

        if has_data and missing:
            question = self.renderer.render_qualification_question(lead_tool.next_question(state))
            return AgentResult(
                text=question,
                provider="state-machine",
                model="rule-based",
                state=state.state,
                reason="missing_fields",
            )

        if lead_tool.is_complete(state) and state.state != "handoff":
            lead = lead_tool.save_qualified_lead(session_id=session_id, state=state, source_channel=source_channel)
            crm_status = lead_tool.crm_stub(lead)
            base_text = self.renderer.render_handoff(lead=lead, crm_reference=crm_status["crm_reference"])

            final_text = base_text
            provider = "state-machine"
            model = "rule-based"

            try:
                rewritten_text, provider, model = await self.llm_service.rewrite_response(base_text, reason="handoff_rewrite")
                final_text = rewritten_text
            except LLMUnavailableError:
                pass

            state = mark_handoff(session=session, state=state, provider=provider, model=model)
            return AgentResult(
                text=final_text,
                provider=provider,
                model=model,
                state=state.state,
                reason="lead_qualified",
            )

        if intent in {"product_lookup", "free_question"}:
            items = product_tool.lookup(
                query=text,
                fallback_culture=state.product,
                fallback_grade=state.grade,
                limit=3,
            )
            template_text = self.renderer.render_product_answer(items)

            if intent == "free_question":
                try:
                    llm_text, provider, model = await self._answer_free_question(
                        text=text,
                        catalog_hint=template_text,
                        history=history,
                    )
                    return AgentResult(
                        text=llm_text,
                        provider=provider,
                        model=model,
                        state=state.state,
                        reason="free_question",
                    )
                except LLMUnavailableError:
                    return AgentResult(
                        text=template_text,
                        provider="template",
                        model="deterministic-template",
                        state=state.state,
                        reason="free_question_fallback",
                    )

            return AgentResult(
                text=template_text,
                provider="product-tool",
                model="rule-based",
                state=state.state,
                reason="product_lookup",
            )

        if not has_data:
            question = self.renderer.render_qualification_question(lead_tool.next_question(state))
            return AgentResult(
                text=question,
                provider="state-machine",
                model="rule-based",
                state=state.state,
                reason="start_qualification",
            )

        return AgentResult(
            text=self.renderer.render_fallback(),
            provider="template",
            model="deterministic-template",
            state=state.state,
            reason="fallback",
        )
