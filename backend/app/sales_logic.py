import re
from datetime import datetime

from sqlmodel import Session, select

from .models import ChatSession, ConversationState, Lead

REQUIRED_FIELDS = ["product", "grade", "volume_tons", "region", "delivery_term", "contact"]

QUESTION_BY_FIELD = {
    "product": "С какой культурой работаем: пшеница, ячмень или кукуруза?",
    "grade": "Класс/качество какой нужен?",
    "volume_tons": "Какой объем в тоннах нужен?",
    "region": "Куда везем, какой регион доставки?",
    "delivery_term": "По сроку как: срочно, завтра или планово?",
    "contact": "Оставьте телефон или email, чтобы менеджер закрепил условия.",
}


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
        r"(?:в|до|по|доставка\s+в|везем\s+в)\s+([А-Яа-яA-Za-z\-\s]{3,50})",
        r"регион\s+([А-Яа-яA-Za-z\-\s]{3,50})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip(" .,")
            if value:
                return value
    return ""


def _extract_delivery_term(text: str) -> str:
    s = text.lower()
    if any(token in s for token in ["срочно", "сегодня", "завтра"]):
        return "срочно"
    match = re.search(r"(?:срок|дата|отгрузк[аи]|поставка)\s*[:\-]?\s*([^\n\.]{3,80})", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip(" .,")
    return ""


def _extract_contact(text: str) -> str:
    phone = re.search(r"(?:\+7|8)[\d\s\-\(\)]{9,}", text)
    if phone:
        return phone.group(0).strip()
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if email:
        return email.group(0).strip()
    telegram = re.search(r"@([A-Za-z0-9_]{5,32})", text)
    if telegram:
        return telegram.group(0).strip()
    return ""


def _missing_fields(state: ConversationState) -> list[str]:
    missing = []
    for field_name in REQUIRED_FIELDS:
        if not getattr(state, field_name):
            missing.append(field_name)
    return missing


def _get_or_create_state(session: Session, session_id: int) -> ConversationState:
    state = session.exec(select(ConversationState).where(ConversationState.session_id == session_id)).first()
    if state:
        return state
    state = ConversationState(session_id=session_id, state="greeting")
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def _get_or_create_lead(session: Session, session_id: int) -> Lead:
    lead_candidates = session.exec(select(Lead).where(Lead.session_id == session_id).order_by(Lead.updated_at)).all()
    lead = lead_candidates[-1] if lead_candidates else None
    if lead:
        return lead
    lead = Lead(session_id=session_id, source="chat", status="new")
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def _sync_session_marker(session: Session, session_id: int, state_value: str) -> None:
    chat_session = session.get(ChatSession, session_id)
    if not chat_session:
        return
    chat_session.last_state = state_value
    chat_session.updated_at = datetime.utcnow()
    session.add(chat_session)


def sync_state_and_lead(session: Session, session_id: int, text: str) -> ConversationState:
    state = _get_or_create_state(session, session_id)
    lead = _get_or_create_lead(session, session_id)

    product = _extract_product(text)
    grade = _extract_grade(text)
    volume = _extract_volume(text)
    region = _extract_region(text)
    delivery_term = _extract_delivery_term(text)
    contact = _extract_contact(text)

    if product:
        state.product = product
        lead.product = product
    if grade:
        state.grade = grade
        lead.grade = grade
    if volume:
        state.volume_tons = volume
        lead.volume_tons = volume
    if region:
        state.region = region
        lead.region = region
    if delivery_term:
        state.delivery_term = delivery_term
        lead.delivery_term = delivery_term
    if contact:
        state.contact = contact
        if "@" in contact and not contact.startswith("@"):
            lead.email = contact
        else:
            lead.phone = contact

    missing = _missing_fields(state)
    state.missing_fields = ",".join(missing)

    if state.state == "stopped_toxic":
        lead.status = "blocked"
    elif not any([state.product, state.grade, state.volume_tons, state.region, state.delivery_term, state.contact]):
        state.state = "greeting"
        lead.status = "new"
    elif missing:
        state.state = "qualification"
        lead.status = "in_progress"
    else:
        if state.state in {"offer", "handoff"}:
            state.state = "handoff"
        else:
            state.state = "offer"
        lead.status = "qualified"

    now = datetime.utcnow()
    state.updated_at = now
    lead.updated_at = now

    session.add(state)
    session.add(lead)
    _sync_session_marker(session, session_id, state.state)
    session.commit()
    session.refresh(state)
    return state


def next_qualification_question(state: ConversationState) -> str:
    missing = _missing_fields(state)
    if not missing:
        return "Параметры собрал. Передаю менеджеру, он закрепит цену и логистику."
    return QUESTION_BY_FIELD[missing[0]]


def mark_handoff(session: Session, state: ConversationState, provider: str, model: str) -> ConversationState:
    state.state = "handoff"
    state.last_provider = provider
    state.last_model = model
    state.updated_at = datetime.utcnow()
    session.add(state)

    lead_candidates = session.exec(select(Lead).where(Lead.session_id == state.session_id).order_by(Lead.updated_at)).all()
    lead = lead_candidates[-1] if lead_candidates else None
    if lead:
        lead.status = "qualified"
        lead.updated_at = datetime.utcnow()
        session.add(lead)

    _sync_session_marker(session, state.session_id, state.state)
    session.commit()
    session.refresh(state)
    return state
