from dataclasses import dataclass


@dataclass
class GuardrailDecision:
    allowed: bool
    answer: str
    toxicity_level: int = 0
    reason: str = ""


SEVERE_TOXIC = {
    "иди нахуй": "Ок, если передумаешь — пиши. Цены нормальные.",
    "пошел нахуй": "Принял. Если вернешься по зерну — отработаем быстро.",
    "рот ебал": "Брат, давай по делу. Что нужно по зерну?",
}

MILD_TOXIC_WORDS = [
    "нах",
    "еб",
    "бля",
    "хуй",
    "сука",
    "долбо",
]

CYBER_BLOCK_WORDS = [
    "ddos",
    "ддос",
    "взлом",
    "ботнет",
    "xss",
    "rce",
    "malware",
    "sql injection",
]


def evaluate_guardrails(text: str) -> GuardrailDecision:
    raw = (text or "").strip()
    s = raw.lower()

    for pattern, answer in SEVERE_TOXIC.items():
        if pattern in s:
            return GuardrailDecision(allowed=False, answer=answer, toxicity_level=2, reason="toxic_hard_stop")

    if any(w in s for w in MILD_TOXIC_WORDS):
        return GuardrailDecision(
            allowed=False,
            answer="Без нервов, брат. Давай нормально: товар, класс, объём и куда везем.",
            toxicity_level=1,
            reason="toxic_soft_stop",
        )

    if any(w in s for w in CYBER_BLOCK_WORDS):
        return GuardrailDecision(
            allowed=False,
            answer="С таким не помогаю. Могу только по зерну: цена, наличие, логистика, заявка.",
            toxicity_level=0,
            reason="security_block",
        )

    return GuardrailDecision(allowed=True, answer="")

