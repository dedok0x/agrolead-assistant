import re
from datetime import datetime

from sqlmodel import Session, select

from .models import ChatSession, ConversationState

REQUIRED_FIELDS = ["product", "grade", "volume_tons", "region", "delivery_term", "contact"]

FIELD_HINT_BY_NAME = {
    "product": "уточни культуру: пшеница, ячмень или кукуруза",
    "grade": "уточни класс или качество зерна",
    "volume_tons": "уточни объем в тоннах",
    "region": "уточни регион или точку доставки",
    "delivery_term": "уточни срок отгрузки или поставки",
    "contact": "уточни контакт для связи: телефон или email",
}

INTENT_PRODUCT_PATTERNS = [
    r"\bцена\b",
    r"\bпрайс\b",
    r"\bстоим",
    r"\bостат",
    r"\bв\s+наличии\b",
    r"\bсколько\b",
    r"\bесть\s+ли\b",
]

INTENT_FREE_QUESTION_PREFIXES = (
    "как",
    "какая",
    "какой",
    "кто",
    "где",
    "когда",
    "сколько",
    "что",
    "почему",
    "че",
    "чё",
)


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _title_preserving_short_words(value: str) -> str:
    raw = _normalize_spaces(value).strip(" .,")
    if not raw:
        return ""
    words = raw.split(" ")
    normalized: list[str] = []
    for word in words:
        if len(word) <= 2:
            normalized.append(word.lower())
        else:
            normalized.append(word[:1].upper() + word[1:].lower())
    return " ".join(normalized)


def _extract_product(text: str) -> str:
    s = text.lower()
    if "пшен" in s:
        return "Пшеница"
    if "ячмен" in s:
        return "Ячмень"
    if "кукуруз" in s:
        return "Кукуруза"
    return ""


def _extract_grade(text: str) -> str:
    s = text.lower()
    class_match = re.search(r"\b([1-6])\s*класс\b", s)
    if class_match:
        return f"{class_match.group(1)} класс"
    if "фураж" in s:
        return "Фуражная"
    if "продов" in s:
        return "Продовольственная"
    return ""


def _extract_volume(text: str) -> str:
    match = re.search(r"(\d+[\.,]?\d*)\s*(?:т|тн|тонн|тонны|тонна)\b", text.lower())
    if not match:
        return ""
    return match.group(1).replace(",", ".")


def _extract_region(text: str) -> str:
    patterns = [
        r"(?:доставка\s+в|вез(?:ем|ти)\s+в|в|до|по)\s+([А-Яа-яA-Za-z\-\s]{3,60})",
        r"регион\s+([А-Яа-яA-Za-z\-\s]{3,60})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1)
        value = re.split(r"[,.;]|\b(срок|контакт|телефон|email|класс|объем)\b", value, maxsplit=1)[0]
        region = _title_preserving_short_words(value)
        if region:
            return region
    return ""


def _extract_delivery_term(text: str) -> str:
    s = text.lower()
    if any(token in s for token in ["срочно", "сегодня", "завтра", "как можно быстрее"]):
        return "срочно"

    match = re.search(r"(?:срок|дата|отгрузк[аи]|поставка)\s*[:\-]?\s*([^\n\.]{3,80})", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return _normalize_spaces(match.group(1).strip(" .,"))


def _extract_contact(text: str) -> str:
    phone = re.search(r"(?:\+7|8)[\d\s\-\(\)]{9,}", text)
    if phone:
        return _normalize_spaces(phone.group(0)).strip()

    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if email:
        return email.group(0).strip()

    telegram = re.search(r"@([A-Za-z0-9_]{5,32})", text)
    if telegram:
        return telegram.group(0).strip()

    return ""


def extract_lead_fields(text: str) -> dict[str, str]:
    return {
        "product": _extract_product(text),
        "grade": _extract_grade(text),
        "volume_tons": _extract_volume(text),
        "region": _extract_region(text),
        "delivery_term": _extract_delivery_term(text),
        "contact": _extract_contact(text),
    }


def missing_required_fields(state: ConversationState) -> list[str]:
    missing: list[str] = []
    for field_name in REQUIRED_FIELDS:
        if not getattr(state, field_name):
            missing.append(field_name)
    return missing


def has_any_lead_data(state: ConversationState) -> bool:
    return any(getattr(state, field_name) for field_name in REQUIRED_FIELDS)


def get_or_create_state(session: Session, session_id: int) -> ConversationState:
    state = session.exec(select(ConversationState).where(ConversationState.session_id == session_id)).first()
    if state:
        return state

    state = ConversationState(session_id=session_id, state="greeting")
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def _sync_session_marker(session: Session, session_id: int, state_value: str) -> None:
    chat_session = session.get(ChatSession, session_id)
    if not chat_session:
        return
    chat_session.last_state = state_value
    chat_session.updated_at = datetime.utcnow()
    session.add(chat_session)


def _advance_state(state: ConversationState) -> None:
    missing = missing_required_fields(state)
    state.missing_fields = ",".join(missing)

    if state.state == "stopped_toxic":
        return

    if not has_any_lead_data(state):
        state.state = "greeting"
        return

    if missing:
        state.state = "qualification"
        return

    if state.state == "handoff":
        return

    state.state = "offer"


def update_state_with_text(session: Session, session_id: int, text: str) -> ConversationState:
    state = get_or_create_state(session, session_id)
    if state.state != "stopped_toxic":
        extracted = extract_lead_fields(text)
        for field_name, value in extracted.items():
            if value:
                setattr(state, field_name, value)

    _advance_state(state)
    state.updated_at = datetime.utcnow()
    session.add(state)
    _sync_session_marker(session, session_id, state.state)
    session.commit()
    session.refresh(state)
    return state


def sync_state_and_lead(session: Session, session_id: int, text: str) -> ConversationState:
    # Совместимость со старым вызовом: теперь синхронизируется только state.
    return update_state_with_text(session=session, session_id=session_id, text=text)


def classify_intent(text: str) -> str:
    normalized = _normalize_spaces(text).lower()
    if not normalized:
        return "lead_capture"

    if any(re.search(pattern, normalized) for pattern in INTENT_PRODUCT_PATTERNS):
        return "product_lookup"

    if normalized.endswith("?") or normalized.startswith(INTENT_FREE_QUESTION_PREFIXES):
        return "free_question"

    return "lead_capture"


def next_qualification_question(state: ConversationState) -> str:
    missing = missing_required_fields(state)
    if not missing:
        return "Параметры собраны."
    return FIELD_HINT_BY_NAME[missing[0]]


def next_missing_field(state: ConversationState) -> str:
    missing = missing_required_fields(state)
    if not missing:
        return ""
    return missing[0]


def next_missing_hint(state: ConversationState) -> str:
    field = next_missing_field(state)
    if not field:
        return ""
    return FIELD_HINT_BY_NAME.get(field, "уточни недостающий параметр сделки")


def mark_handoff(session: Session, state: ConversationState, provider: str, model: str) -> ConversationState:
    state.state = "handoff"
    state.last_provider = provider
    state.last_model = model
    state.updated_at = datetime.utcnow()
    state.missing_fields = ",".join(missing_required_fields(state))
    session.add(state)
    _sync_session_marker(session, state.session_id, state.state)
    session.commit()
    session.refresh(state)
    return state


def mark_toxic_stop(session: Session, state: ConversationState, toxicity_level: int) -> ConversationState:
    state.state = "stopped_toxic"
    state.toxicity_level = max(state.toxicity_level, toxicity_level)
    state.updated_at = datetime.utcnow()
    session.add(state)
    _sync_session_marker(session, state.session_id, state.state)
    session.commit()
    session.refresh(state)
    return state
