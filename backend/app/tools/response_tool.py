from ..models import ConversationState, Lead, ProductItem


class StagePromptBuilder:
    STAGE_SYSTEM_PROMPTS = {
        "greeting": (
            "Ты живой B2B ассистент по зерновым сделкам. "
            "Отвечай по-человечески, без канцелярита и без шаблонных клише."
        ),
        "qualification": (
            "Ты ведешь квалификацию лида в диалоге с клиентом. "
            "Сначала ответь по теме сообщения, затем мягко задай только один следующий уточняющий вопрос."
        ),
        "free_question": (
            "Ты консультируешь по зерновым сделкам и наличию. "
            "Ответ должен быть конкретным, естественным и практичным."
        ),
        "product_lookup": (
            "Ты коммерческий ассистент зерновой компании. "
            "Дай релевантный ответ по товару и условиям без выдумок."
        ),
        "handoff": (
            "Ты формируешь финальный ответ после квалификации лида. "
            "Подтверди параметры и аккуратно обозначь следующий шаг с менеджером."
        ),
        "post_handoff": (
            "Ты продолжаешь диалог после передачи лида менеджеру. "
            "Сохраняй деловой, спокойный и полезный тон."
        ),
    }

    def stage_system_prompt(self, stage: str) -> str:
        return self.STAGE_SYSTEM_PROMPTS.get(stage, self.STAGE_SYSTEM_PROMPTS["free_question"])

    def lead_context(self, state: ConversationState, missing_fields: list[str], next_hint: str) -> str:
        return (
            "Текущее состояние лида:\n"
            f"- product: {state.product or '-'}\n"
            f"- grade: {state.grade or '-'}\n"
            f"- volume_tons: {state.volume_tons or '-'}\n"
            f"- region: {state.region or '-'}\n"
            f"- delivery_term: {state.delivery_term or '-'}\n"
            f"- contact: {state.contact or '-'}\n"
            f"- missing_fields: {', '.join(missing_fields) if missing_fields else 'none'}\n"
            f"- next_hint: {next_hint or 'none'}"
        )

    def catalog_context(self, items: list[ProductItem]) -> str:
        if not items:
            return "Каталог: подходящие позиции не найдены по текущему сообщению."

        lines = []
        for item in items[:3]:
            lines.append(
                f"- {item.name}: {item.price_from:.0f}-{item.price_to:.0f} руб/т, "
                f"остаток {item.stock_tons:.0f} т, локация {item.location}"
            )
        return "Каталог-подсказка:\n" + "\n".join(lines)

    def no_repeat_context(self, last_assistant_messages: list[str]) -> str:
        if not last_assistant_messages:
            return ""
        rows = "\n".join(f"- {message}" for message in last_assistant_messages)
        return (
            "Не повторяй дословно формулировки из последних ответов ассистента:\n"
            f"{rows}"
        )

    def handoff_brief(self, lead: Lead, crm_reference: str) -> str:
        return (
            "Лид квалифицирован. Дай финальное подтверждение и следующий шаг.\n"
            f"Параметры: {lead.product} {lead.grade}, {lead.volume_tons} т, {lead.region}, срок {lead.delivery_term}.\n"
            f"Контакт: {lead.phone or lead.email or '-'}\n"
            f"CRM reference: {crm_reference}"
        )
