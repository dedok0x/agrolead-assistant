import hashlib

from .guardrails import GuardrailDecision


REPLIES_BY_DECISION: dict[str, list[str]] = {
    "empty_input": [
        "Опишите задачу в одном сообщении: товар, объем, регион и контакт.",
        "Давайте по делу: какая культура, какой объем, откуда/куда и как с вами связаться?",
        "Чтобы запустить заявку, нужны 4 вещи: товар, тоннаж, география и контакт.",
    ],
    "security_block": [
        "С запросами по взлому и атакам не помогаю. Могу оформить только коммерческую заявку по зерну и логистике.",
        "Такие темы не поддерживаю. Если нужна сделка по поставке или перевозке, зафиксируем параметры заявки.",
        "С небезопасными сценариями не работаю. Могу помочь только с продажей, закупкой, логистикой и экспортом зерна.",
    ],
    "toxic_hard_stop": [
        "В таком тоне диалог не продолжаю. Вернитесь с деловым запросом по сделке.",
        "Оскорбления не принимаю. Если нужен расчет или заявка, напишите спокойно и предметно.",
        "Продолжим только в рабочем формате: товар, объем, регион и контакт.",
    ],
    "toxic_soft_stop": [
        "Давайте без резких формулировок и сразу к параметрам заявки.",
        "Готов продолжить, если общаемся конструктивно. Напишите товар, объем и направление.",
        "Перейдем к делу: культура, тоннаж, базис/маршрут и контакт.",
    ],
}


DEFAULT_REPLY = "Сформулируйте запрос по сделке: товар, объем, регион и контакт для связи."
_CALL_COUNTER_BY_DECISION: dict[str, int] = {}


def render_guardrail_reply(decision: GuardrailDecision, user_text: str, last_assistant_messages: list[str]) -> str:
    variants = REPLIES_BY_DECISION.get(decision.decision_code, [DEFAULT_REPLY])
    if not variants:
        return DEFAULT_REPLY

    # Deterministic variability per input text + decision code.
    seed = hashlib.sha256(f"{decision.decision_code}:{user_text}".encode("utf-8")).hexdigest()
    counter = _CALL_COUNTER_BY_DECISION.get(decision.decision_code, 0)
    _CALL_COUNTER_BY_DECISION[decision.decision_code] = counter + 1
    start = (int(seed[:8], 16) + counter) % len(variants)

    recent = {(item or "").strip().lower() for item in (last_assistant_messages or [])[-3:] if item}
    for offset in range(len(variants)):
        candidate = variants[(start + offset) % len(variants)]
        if candidate.lower() not in recent:
            return candidate
    return variants[start]
