from datetime import datetime

from sqlmodel import Session, select

from ..models import ChatMessage, ConversationState, Lead
from ..sales_logic import missing_required_fields, next_missing_field, next_missing_hint, update_state_with_text


class LeadTool:
    def __init__(self, session: Session) -> None:
        self.session = session

    def update_state(self, session_id: int, text: str) -> ConversationState:
        return update_state_with_text(session=self.session, session_id=session_id, text=text)

    def missing_fields(self, state: ConversationState) -> list[str]:
        return missing_required_fields(state)

    def is_complete(self, state: ConversationState) -> bool:
        return len(self.missing_fields(state)) == 0

    def next_field(self, state: ConversationState) -> str:
        return next_missing_field(state)

    def next_hint(self, state: ConversationState) -> str:
        return next_missing_hint(state)

    def _dialogue_snapshot(self, session_id: int, limit: int = 30) -> str:
        rows = list(self.session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)).all())
        rows.sort(key=lambda item: item.created_at)
        if not rows:
            return ""

        recent = rows[-limit:]
        parts = []
        for message in recent:
            role = "user" if message.role == "user" else "assistant"
            parts.append(f"{role}: {message.text}")
        return "\n".join(parts)

    def _get_latest_lead(self, session_id: int) -> Lead | None:
        leads = list(self.session.exec(select(Lead).where(Lead.session_id == session_id)).all())
        leads.sort(key=lambda item: item.updated_at)
        if not leads:
            return None
        return leads[-1]

    def save_qualified_lead(self, session_id: int, state: ConversationState, source_channel: str = "web") -> Lead:
        lead = self._get_latest_lead(session_id=session_id)
        now = datetime.utcnow()

        if not lead:
            lead = Lead(session_id=session_id, created_at=now)

        lead.product = state.product
        lead.grade = state.grade
        lead.volume_tons = state.volume_tons
        lead.region = state.region
        lead.delivery_term = state.delivery_term
        lead.status = "qualified"
        lead.source = "chat"
        lead.source_channel = source_channel or "web"
        lead.raw_dialogue = self._dialogue_snapshot(session_id=session_id)
        lead.updated_at = now

        contact = (state.contact or "").strip()
        if contact:
            if "@" in contact and not contact.startswith("@"):
                lead.email = contact
            else:
                lead.phone = contact

        self.session.add(lead)
        self.session.commit()
        self.session.refresh(lead)
        return lead

    def crm_stub(self, lead: Lead) -> dict[str, str]:
        return {
            "crm_status": "queued",
            "crm_reference": f"crm-{lead.id}",
        }
