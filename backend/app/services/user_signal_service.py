from __future__ import annotations

import re

from ..domain.sales import NextAction, UserSignal


class UserSignalService:
    def classify(self, user_message: str, current_next_action: str | None = None) -> str:
        text = self._normalize(user_message)
        if not text:
            return UserSignal.REFUSAL_OR_UNKNOWN.value

        if self._has(text, [
            "просто проконсульт",
            "просто консульт",
            "проконсультируй",
            "консультация",
            "хочу консультацию",
            "просто проконсультируй кто вы",
        ]):
            return UserSignal.CONSULTATION_REQUEST.value
        if self._has(text, [
            "ты кто",
            "вы кто",
            "кто вы",
            "кто ты",
            "что ты такое",
            "кто такие",
        ]):
            return UserSignal.ASKS_IDENTITY.value
        if self._has(text, [
            "не блещешь",
            "туп",
            "треш",
            "не понимаешь",
            "плохой бот",
            "интеллектом",
            "ерунду",
            "чушь",
        ]):
            return UserSignal.NEGATIVE_FEEDBACK.value
        if self._has(text, [
            "уточни",
            "объясни",
            "скажи проще",
            "переформулируй",
            "что значит",
        ]) or text == "просто":
            return UserSignal.META_DIALOGUE.value

        if self._has(text, ["чё можешь", "че можешь", "что можешь", "что умеешь", "помоги", "какие услуги", "что умеете"]):
            return UserSignal.ASKS_CAPABILITIES.value
        if text in {"что", "чего", "в смысле", "не понял", "не поняла", "как это", "а?", "?"}:
            return UserSignal.ASKS_CLARIFICATION.value
        if self._has(text, ["пипец", "пипейц", "блин", "чё за", "че за", "сложно", "достало"]):
            return UserSignal.FRUSTRATION.value
        if self._has(text, ["дорого", "цена", "почем", "почём", "стоимость", "сколько стоит"]):
            return UserSignal.OBJECTION_PRICE.value
        if self._has(text, ["менеджер", "человек", "оператор", "позвоните", "свяжитесь"]):
            return UserSignal.WANTS_HUMAN.value
        if self._has(text, ["не знаю", "никакой", "без понятия", "потом скажу", "неважно", "нет данных"]):
            return UserSignal.REFUSAL_OR_UNKNOWN.value
        if current_next_action == NextAction.ASK_VOLUME.value and text in {"большой", "много", "нормально", "крупный", "немало", "побольше"}:
            return UserSignal.VAGUE_ANSWER.value
        if text in {"привет", "здравствуйте", "добрый день", "ок", "ладно", "ага"}:
            return UserSignal.SMALLTALK.value
        if self._has(text, ["погода", "анекдот", "рецепт", "фильм", "музыка", "политика"]):
            return UserSignal.IRRELEVANT.value
        return UserSignal.PROVIDES_FACT.value

    @staticmethod
    def _has(text: str, markers: list[str]) -> bool:
        return any(marker in text for marker in markers)

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))
