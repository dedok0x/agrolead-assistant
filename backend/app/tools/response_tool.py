from ..models import Lead, ProductItem


class ResponseRenderer:
    def render_qualification_question(self, question: str) -> str:
        return question.strip()

    def render_product_answer(self, items: list[ProductItem]) -> str:
        if not items:
            return "Сейчас точной позиции в каталоге не вижу. Назовите культуру, класс и объем - подберу вариант."

        top = items[0]
        price = f"{top.price_from:.0f}-{top.price_to:.0f} руб/т" if top.price_from or top.price_to else "по запросу"
        stock = f"остаток {top.stock_tons:.0f} т" if top.stock_tons else "остаток уточняется"
        return f"Есть {top.name}: {price}, {stock}, регион {top.location}. Если подходит, назовите объем и срок поставки."

    def render_handoff(self, lead: Lead, crm_reference: str) -> str:
        return (
            f"Заявку зафиксировал: {lead.product} {lead.grade}, {lead.volume_tons} т, {lead.region}. "
            f"Передаю менеджеру, номер обращения {crm_reference}."
        )

    def render_fallback(self) -> str:
        return "Принял. Уточните товар, класс, объем и регион доставки, чтобы оформить заявку."
