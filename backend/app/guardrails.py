import os
import re
from dataclasses import dataclass


@dataclass(slots=True)
class GuardrailDecision:
    allowed: bool
    answer: str
    reason: str
    toxicity_level: int = 0
    stop_dialogue: bool = False


SEVERE_TOXIC_PATTERNS = [
    r"иди\s+на\s*х[уюй]",
    r"пош[её]л\s+на\s*х[уюй]",
    r"рот\s+е?б",
    r"уеб[иы]вай",
]

MILD_TOXIC_PATTERNS = [
    r"\bбля(?:дь)?\b",
    r"\bсука\b",
    r"\bдолбо[её]б\w*",
    r"\bнах\b",
    r"\bхер\b",
]

SECURITY_BLOCK_PATTERNS = [
    r"\bddos\b",
    r"\bботнет\b",
    r"\brce\b",
    r"\bxss\b",
    r"\bsql\s*injection\b",
    r"взлом",
    r"малвар",
    r"фишинг",
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def evaluate_guardrails(text: str) -> GuardrailDecision:
    strict_mode = os.getenv("TOXIC_STRICT_MODE", "1").lower() not in {"0", "false", "no"}
    normalized = _normalize(text)

    if not normalized:
        return GuardrailDecision(allowed=False, answer="Напиши сообщение по делу: товар, класс, объем.", reason="empty_input")

    if _matches_any(SECURITY_BLOCK_PATTERNS, normalized):
        return GuardrailDecision(
            allowed=False,
            answer="С такими запросами не работаю. Могу помочь только по зерну: товар, объем, логистика и заявка.",
            reason="security_block",
            toxicity_level=0,
            stop_dialogue=True,
        )

    if _matches_any(SEVERE_TOXIC_PATTERNS, normalized):
        return GuardrailDecision(
            allowed=False,
            answer="С таким тоном не работаю. Если нужен расчет по зерну, вернись с нормальным запросом.",
            reason="toxic_hard_stop",
            toxicity_level=2,
            stop_dialogue=True,
        )

    if _matches_any(MILD_TOXIC_PATTERNS, normalized):
        if strict_mode:
            return GuardrailDecision(
                allowed=False,
                answer="Давай спокойно и по делу: товар, класс, объем и куда везем.",
                reason="toxic_soft_stop",
                toxicity_level=1,
                stop_dialogue=True,
            )
        return GuardrailDecision(
            allowed=True,
            answer="",
            reason="toxic_warn_allowed",
            toxicity_level=1,
            stop_dialogue=False,
        )

    return GuardrailDecision(allowed=True, answer="", reason="ok", toxicity_level=0, stop_dialogue=False)
