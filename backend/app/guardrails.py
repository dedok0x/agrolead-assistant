import os
import re
from dataclasses import dataclass


@dataclass(slots=True)
class GuardrailDecision:
    allowed: bool
    decision_code: str
    severity: int
    reason: str
    stop_dialogue: bool = False
    policy_tags: tuple[str, ...] = ()


SEVERE_TOXIC_PATTERNS = [
    r"\bхуй\b",
    r"\bнахуй\b",
    r"иди\s+на\s*х[уюй]",
    r"пош[её]л\s+на\s*х[уюй]",
    r"рот\s+е?б",
    r"уеб[иы]вай",
]

MILD_TOXIC_PATTERNS = [
    r"\bбля\b",
    r"\bблять\b",
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
        return GuardrailDecision(
            allowed=False,
            decision_code="empty_input",
            severity=1,
            reason="empty_input",
            stop_dialogue=False,
            policy_tags=("input", "empty"),
        )

    if _matches_any(SECURITY_BLOCK_PATTERNS, normalized):
        return GuardrailDecision(
            allowed=False,
            decision_code="security_block",
            severity=3,
            reason="security_block",
            stop_dialogue=True,
            policy_tags=("security", "block"),
        )

    if _matches_any(SEVERE_TOXIC_PATTERNS, normalized):
        return GuardrailDecision(
            allowed=False,
            decision_code="toxic_hard_stop",
            severity=3,
            reason="toxic_hard_stop",
            stop_dialogue=True,
            policy_tags=("toxicity", "hard-stop"),
        )

    if _matches_any(MILD_TOXIC_PATTERNS, normalized):
        if strict_mode:
            return GuardrailDecision(
                allowed=False,
                decision_code="toxic_soft_stop",
                severity=2,
                reason="toxic_soft_stop",
                stop_dialogue=True,
                policy_tags=("toxicity", "soft-stop"),
            )
        return GuardrailDecision(
            allowed=True,
            decision_code="toxic_warn_allowed",
            severity=1,
            reason="toxic_warn_allowed",
            stop_dialogue=False,
            policy_tags=("toxicity", "warn"),
        )

    return GuardrailDecision(
        allowed=True,
        decision_code="ok",
        severity=0,
        reason="ok",
        stop_dialogue=False,
        policy_tags=("ok",),
    )
