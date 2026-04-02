import re
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from .models import ConversationState, Lead


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
    m = re.search(r"([1-6])\s*класс", s)
    if m:
        return f"{m.group(1)} класс"
    if "фураж" in s:
        return "Фуражная"
    return ""


def _extract_volume(text: str) -> str:
    s = text.lower()
    m = re.search(r"(\d+[\.,]?\d*)\s*(?:т|тонн|тонны|тонна)", s)
    return m.group(1).replace(",", ".") if m else ""


def _extract_region(text: str) -> str:
    m = re.search(r"(?:в|до|по)\s+([А-Яа-яA-Za-z\-\s]{3,40})", text)
    return m.group(1).strip(" .,") if m else ""


def _extract_delivery_term(text: str) -> str:
    s = text.lower()
    if any(x in s for x in ["срочно", "сегодня", "завтра"]):
        return "срочно"
    if any(x in s for x in ["недел", "месяц", "дата", "срок"]):
        return text[:80]
    return ""


def _extract_contact(text: str) -> str:
    phone = re.search(r"(?:\+7|8)[\s\-\(\)]*\d[\d\s\-\(\)]{8,}", text)
    if phone:
        return phone.group(0).strip()
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if email:
        return email.group(0).strip()
    return ""


def get_or_create_state(session: Session, session_id: int) -> ConversationState:
    state = session.exec(select(ConversationState).where(ConversationState.session_id == session_id)).first()
    if state:
        return state
    state = ConversationState(session_id=session_id, state="greeting")
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def sync_state_and_lead(session: Session, session_id: int, text: str) -> ConversationState:
    lead = session.exec(select(Lead).where(Lead.session_id == session_id).order_by(Lead.updated_at.desc())).first()
    if not lead:
        lead = Lead(session_id=session_id, source="chat", status="new")

    state = get_or_create_state(session, session_id)

    product = _extract_product(text)
    grade = _extract_grade(text)
    volume = _extract_volume(text)
    region = _extract_region(text)
    delivery_term = _extract_delivery_term(text)
    contact = _extract_contact(text)

    if product:
        lead.product = product
        state.product = product
    if grade:
        lead.grade = grade
        state.grade = grade
    if volume:
        lead.volume_tons = volume
        state.volume_tons = volume
    if region:
        lead.region = region
        state.region = region
    if delivery_term:
        lead.delivery_term = delivery_term
        state.delivery_term = delivery_term
    if contact:
        if "@" in contact:
            lead.email = contact
        else:
            lead.phone = contact
        state.contact = contact

    if all([state.product, state.grade, state.volume_tons, state.region, state.contact]):
        state.state = "qualified"
        lead.status = "qualified"
    else:
        state.state = "qualification"
        lead.status = "in_progress"

    lead.updated_at = datetime.utcnow()
    state.updated_at = datetime.utcnow()
    session.add(lead)
    session.add(state)
    session.commit()
    return state


def next_qualification_question(state: ConversationState) -> str:
    if not state.product:
        return "Поехали по делу: пшеница, ячмень или кукуруза?"
    if not state.grade:
        return "Класс какой нужен?"
    if not state.volume_tons:
        return "Какой объем в тоннах берете?"
    if not state.region:
        return "Куда везем, какой регион?"
    if not state.delivery_term:
        return "По сроку как: срочно или есть окно?"
    if not state.contact:
        return "Оставь телефон или почту, закрепим условия у менеджера."
    return "Заявка собрана. Передаю менеджеру, он закрепит цену и логистику."

