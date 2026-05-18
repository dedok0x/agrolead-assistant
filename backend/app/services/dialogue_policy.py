from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain.sales import NextAction, UserSignal, request_type_label
from .field_extractor import ExtractedFact


@dataclass(slots=True)
class DialoguePolicyResult:
    response_strategy: str
    should_repeat_question: bool = False
    reformulated_question: str = ""
    clarification_options: list[str] = field(default_factory=list)
    should_save_fact: dict[str, bool] = field(default_factory=dict)
    fallback_text: str | None = None


class DialoguePolicy:
    def choose(
        self,
        *,
        user_message: str,
        current_next_action: str | None,
        next_action: str,
        known_facts: dict[str, Any],
        missing_fields: list[str],
        extracted_facts: list[ExtractedFact],
        user_signal: str,
        attempts: dict[str, int] | None = None,
    ) -> DialoguePolicyResult:
        attempts = attempts or {}
        save = {fact.field: fact.status == "confirmed" for fact in extracted_facts}
        uncertain = [fact for fact in extracted_facts if fact.status == "uncertain"]

        if user_signal == UserSignal.ASKS_CAPABILITIES.value:
            return DialoguePolicyResult(
                response_strategy="capabilities",
                should_save_fact=save,
                fallback_text=(
                    "Я могу помочь оформить заявку по продаже или покупке зерна, логистике, хранению и ВЭД. "
                    "Обычно собираю культуру, объем, регион или маршрут, сроки и контакт, а потом передаю менеджеру. "
                    "Начнем с продажи, покупки или логистики?"
                ),
            )
        if user_signal == UserSignal.ASKS_IDENTITY.value:
            return DialoguePolicyResult(
                response_strategy="identity",
                should_save_fact=save,
                fallback_text=(
                    "Я AI-ассистент ПЕТРОХЛЕБ-КУБАНЬ. Помогаю с заявками по зерну, логистике, хранению и ВЭД: могу просто проконсультировать, "
                    "а если задача созреет, аккуратно соберу данные для менеджера. Скажите своими словами, что хотите понять?"
                ),
            )
        if user_signal == UserSignal.CONSULTATION_REQUEST.value:
            return DialoguePolicyResult(
                response_strategy="consultation",
                should_save_fact={**save, "product": False, "volume": False, "region": False},
                fallback_text=(
                    "Да, можем без анкеты: просто проконсультирую. Я AI-ассистент ПЕТРОХЛЕБ-КУБАНЬ, работаю по закупке и продаже зерна, логистике, хранению и ВЭД. "
                    "Что разобрать в первую очередь?"
                ),
            )
        if user_signal == UserSignal.META_DIALOGUE.value:
            return DialoguePolicyResult(
                response_strategy="meta_dialogue",
                should_save_fact={**save, "product": False, "volume": False, "region": False},
                fallback_text=(
                    "Проще так: я могу либо ответить на вопрос по компании и услугам, либо помочь оформить заявку. "
                    "Если нужна консультация, просто напишите тему, например: продажа зерна, покупка, логистика, хранение или экспорт."
                ),
            )
        if user_signal == UserSignal.NEGATIVE_FEEDBACK.value:
            return DialoguePolicyResult(
                response_strategy="recover_feedback",
                should_save_fact={**save, "product": False, "volume": False, "region": False},
                fallback_text=(
                    "Справедливо, я слишком рано ушел в анкету. Давайте нормально: я AI-ассистент ПЕТРОХЛЕБ-КУБАНЬ, могу сначала просто проконсультировать без заявки. "
                    "Напишите вопрос своими словами, а параметры сделки будем собирать только если это действительно понадобится."
                ),
            )
        if user_signal == UserSignal.ASKS_CLARIFICATION.value:
            return DialoguePolicyResult(
                response_strategy="clarification",
                should_save_fact=save,
                fallback_text=(
                    "Я уточняю тип заявки. Проще говоря: вы хотите продать зерно, купить зерно, "
                    "рассчитать перевозку или просто проконсультироваться?"
                ),
            )
        if user_signal == UserSignal.FRUSTRATION.value:
            return DialoguePolicyResult(
                response_strategy="deescalate",
                should_save_fact=save,
                fallback_text=self._frustration_text(current_next_action or next_action),
            )
        if user_signal == UserSignal.VAGUE_ANSWER.value:
            return DialoguePolicyResult(
                response_strategy="vague_options",
                should_save_fact=save,
                fallback_text=self._vague_text(current_next_action or next_action),
            )
        if user_signal == UserSignal.REFUSAL_OR_UNKNOWN.value:
            return DialoguePolicyResult(
                response_strategy="skip_or_consultation",
                should_save_fact=save,
                fallback_text=self._refusal_text(current_next_action or next_action),
            )
        if user_signal == UserSignal.WANTS_HUMAN.value:
            return DialoguePolicyResult(
                response_strategy="human_handoff",
                should_save_fact=save,
                fallback_text="Могу передать менеджеру. Чтобы он связался с вами, оставьте телефон, Telegram или email.",
            )

        if uncertain:
            fact = uncertain[0]
            return DialoguePolicyResult(
                response_strategy="confirm_uncertain_fact",
                should_save_fact=save,
                fallback_text=self._uncertain_text(fact),
            )

        confirmed = [fact for fact in extracted_facts if fact.status == "confirmed"]
        if confirmed:
            return DialoguePolicyResult(
                response_strategy="ack_fact",
                should_save_fact=save,
                fallback_text=self._ack_text(confirmed[-1], next_action, known_facts),
            )

        field = self._field_for_action(current_next_action or next_action)
        if field and attempts.get(field, 0) > 1:
            return DialoguePolicyResult(
                response_strategy="reformulate",
                should_repeat_question=False,
                reformulated_question=self._question(next_action, repeated=True),
                clarification_options=self._options_for(next_action),
                should_save_fact=save,
                fallback_text=self._question(next_action, repeated=True),
            )

        return DialoguePolicyResult(
            response_strategy="default",
            should_repeat_question=True,
            reformulated_question=self._question(next_action, repeated=False),
            should_save_fact=save,
            fallback_text=None,
        )

    def _ack_text(self, fact: ExtractedFact, next_action: str, known_facts: dict[str, Any]) -> str:
        value = fact.normalized_value
        if fact.field == "request_type":
            return f"Понял, {request_type_label(str(value))}. {self._question(next_action, repeated=False)}"
        if fact.field == "product":
            return f"Понял, фиксирую: {value}. {self._question(next_action, repeated=False)}"
        if fact.field == "volume":
            return f"Понял, {value}. {self._question(next_action, repeated=False)}"
        if fact.field == "region":
            return f"Принял, {value}. {self._question(next_action, repeated=False)}"
        if fact.field == "timing":
            return f"Понял, срок поставим как \"{value}\". {self._question(next_action, repeated=False)}"
        if fact.field == "contact":
            if next_action == NextAction.HANDOFF_MANAGER.value:
                return self._handoff_summary({**known_facts, "contact": value})
            return f"Контакт получил. {self._question(next_action, repeated=False)}"
        return self._question(next_action, repeated=False)

    @staticmethod
    def _uncertain_text(fact: ExtractedFact) -> str:
        if fact.field == "product" and fact.normalized_value == "гречиха":
            if "гречк" in (fact.source_text or "").lower():
                return "Гречка обычно относится к крупе, а в зерновой заявке можно зафиксировать гречиху. Подтвердить культуру как гречиха?"
            return "Вы имеете в виду гречиху? Могу зафиксировать ее как культуру для заявки."
        if fact.field == "volume":
            return f"Правильно понял: {fact.normalized_value}?"
        return f"Правильно понял: {fact.normalized_value}?"

    @staticmethod
    def _handoff_summary(facts: dict[str, Any]) -> str:
        rows = []
        if facts.get("request_type"):
            rows.append(request_type_label(str(facts["request_type"])))
        if facts.get("product"):
            rows.append(str(facts["product"]))
        if facts.get("volume"):
            rows.append(str(facts["volume"]))
        if facts.get("region"):
            rows.append(str(facts["region"]))
        if facts.get("timing"):
            rows.append(f"срок {facts['timing']}")
        summary = ", ".join(rows)
        return f"Заявка собрана: {summary}. Передаю менеджеру." if summary else "Заявка собрана. Передаю менеджеру."

    @staticmethod
    def _frustration_text(action: str) -> str:
        if action == NextAction.ASK_VOLUME.value:
            return "Да, давайте проще. Можно без точной цифры — хотя бы примерно: до 100 тонн, 100–500 или больше 500?"
        return "Да, давайте проще. Напишите как удобно: продажа, покупка, логистика или консультация."

    @staticmethod
    def _vague_text(action: str) -> str:
        if action == NextAction.ASK_VOLUME.value:
            return "Понял, объем крупный. Уточните примерный диапазон: до 100 тонн, 100–500, 500–1000 или больше 1000?"
        return "Понял. Можно ответить примерно или написать \"уточнить позже\"."

    @staticmethod
    def _refusal_text(action: str) -> str:
        if action == NextAction.ASK_VOLUME.value:
            return "Тогда не буду фиксировать объем. Можем оформить как консультацию или оставить объем \"уточнить позже\". Как удобнее?"
        if action == NextAction.ASK_TIMING.value:
            return "Можно оставить срок как \"по согласованию\". Если подходит, напишите контакт для связи."
        return "Понял. Можем пропустить этот параметр и оформить консультацию, либо вернуться к нему позже."

    @staticmethod
    def _question(action: str, *, repeated: bool) -> str:
        if action == NextAction.ASK_REQUEST_TYPE.value:
            return "Проще говоря: вы хотите продать зерно, купить зерно, рассчитать перевозку или просто проконсультироваться?"
        if action == NextAction.ASK_PRODUCT.value:
            return "Какую культуру фиксируем?"
        if action == NextAction.ASK_VOLUME.value:
            return "Какой объем рассматриваете?" if not repeated else "Укажите хотя бы диапазон: до 100 тонн, 100–500, 500–1000 или больше 1000?"
        if action == NextAction.ASK_REGION.value:
            return "Уточните регион или маршрут поставки."
        if action == NextAction.ASK_TIMING.value:
            return "В какие сроки нужна сделка или поставка?"
        if action == NextAction.ASK_CONTACT.value:
            return "Остался контакт для связи — телефон, Telegram или email."
        if action == NextAction.HANDOFF_MANAGER.value:
            return "Заявка собрана. Передаю менеджеру."
        return "Уточните следующий параметр заявки."

    @staticmethod
    def _options_for(action: str) -> list[str]:
        if action == NextAction.ASK_VOLUME.value:
            return ["до 100 тонн", "100–500", "500–1000", "больше 1000", "уточнить позже"]
        if action == NextAction.ASK_REQUEST_TYPE.value:
            return ["продажа", "покупка", "логистика", "консультация"]
        return ["уточнить позже", "консультация"]

    @staticmethod
    def _field_for_action(action: str) -> str:
        return {
            NextAction.ASK_REQUEST_TYPE.value: "request_type",
            NextAction.ASK_PRODUCT.value: "product",
            NextAction.ASK_VOLUME.value: "volume",
            NextAction.ASK_REGION.value: "region",
            NextAction.ASK_TIMING.value: "timing",
            NextAction.ASK_CONTACT.value: "contact",
        }.get(action, "")
