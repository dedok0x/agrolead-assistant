from __future__ import annotations

import re

from ..domain.sales import NextAction, UserSignal


class UserSignalService:
    def classify(self, user_message: str, current_next_action: str | None = None) -> str:
        text = self._normalize(user_message)
        if not text:
            return UserSignal.REFUSAL_OR_UNKNOWN.value

        if any(marker in text for marker in ["чё можешь", "че можешь", "что можешь", "что умеешь", "помоги", "какие услуги", "что умеете"]):
            return UserSignal.ASKS_CAPABILITIES.value
        if text in {"что", "чего", "в смысле", "не понял", "не поняла", "как это", "а?", "?"}:
            return UserSignal.ASKS_CLARIFICATION.value
        if any(marker in text for marker in ["пипец", "пипейц", "блин", "чё за", "че за", "сложно", "достало"]):
            return UserSignal.FRUSTRATION.value
        if any(marker in text for marker in ["дорого", "цена", "почем", "почём", "стоимость", "сколько стоит"]):
            return UserSignal.OBJECTION_PRICE.value
        if any(marker in text for marker in ["менеджер", "человек", "оператор", "позвоните", "свяжитесь"]):
            return UserSignal.WANTS_HUMAN.value
        if any(marker in text for marker in ["не знаю", "никакой", "без понятия", "потом скажу", "неважно", "нет данных"]):
            return UserSignal.REFUSAL_OR_UNKNOWN.value
        if current_next_action == NextAction.ASK_VOLUME.value and text in {"большой", "много", "нормально", "крупный", "немало", "побольше"}:
            return UserSignal.VAGUE_ANSWER.value
        if text in {"привет", "здравствуйте", "добрый день", "ок", "ладно", "ага"}:
            return UserSignal.SMALLTALK.value
        if any(marker in text for marker in ["погода", "анекдот", "рецепт", "фильм", "музыка", "политика"]):
            return UserSignal.IRRELEVANT.value
        return UserSignal.PROVIDES_FACT.value

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))
