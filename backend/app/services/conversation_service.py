from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from ..db import engine
from ..domain.sales import DB_REQUEST_TYPE_BY_V7, NextAction, REQUIRED_FIELDS, SalesStage
from ..guardrail_response_policy import render_guardrail_reply
from ..guardrails import evaluate_guardrails
from ..llm_service import LLMService
from ..models import (
    ChatExtractedFact,
    ChatMessage,
    ChatMissingField,
    ChatQualificationCheckpoint,
    ChatSession,
    CrmLead,
    CrmLeadContactSnapshot,
    CrmLeadItem,
    RefCommodity,
    RefLeadSource,
    RefPipelineStage,
    RefRegion,
    RefRequestType,
)
from ..schemas.chat import ConversationResult
from .gigachat_service import GigaChatComposer
from .dialogue_policy import DialoguePolicy
from .lead_qualification_service import LeadQualificationService
from .rag_service import RAGService
from .response_validator import ResponseValidator
from .sales_engine import SalesEngine
from .user_signal_service import UserSignalService

LOGGER = logging.getLogger("agrolead.conversation")


class ConversationService:
    def __init__(self, llm_service: LLMService) -> None:
        self.qualifier = LeadQualificationService()
        self.sales_engine = SalesEngine(self.qualifier)
        self.signal_service = UserSignalService()
        self.dialogue_policy = DialoguePolicy()
        self.validator = ResponseValidator()
        self.composer = GigaChatComposer(llm_service=llm_service, validator=self.validator)

    async def handle_message(
        self,
        text: str,
        session_id: str | int | None = None,
        client_id: str | None = None,
        source_channel: str = "web_widget",
        external_user_id: str | None = None,
        metadata: dict | None = None,
        db_session: Session | None = None,
    ) -> ConversationResult:
        if db_session is None:
            with Session(engine) as session:
                return await self._handle_message(
                    session=session,
                    text=text,
                    session_id=session_id,
                    client_id=client_id,
                    source_channel=source_channel,
                    external_user_id=external_user_id,
                    metadata=metadata,
                )
        return await self._handle_message(
            session=db_session,
            text=text,
            session_id=session_id,
            client_id=client_id,
            source_channel=source_channel,
            external_user_id=external_user_id,
            metadata=metadata,
        )

    def get_status(
        self,
        *,
        db_session: Session,
        external_user_id: str,
        source_channel: str = "telegram",
    ) -> dict[str, Any]:
        source = self._source(db_session, source_channel)
        chat = (
            db_session.exec(
                select(ChatSession)
                .where(ChatSession.source_id == source.id, ChatSession.external_user_id == external_user_id)
                .order_by(ChatSession.updated_at.desc())
            ).first()
            if source and source.id
            else None
        )
        if not chat or not chat.id:
            decision = self.sales_engine.handle("", {})
            return {
                "session_id": None,
                "lead_id": None,
                "known_facts": {},
                "missing_fields": decision.missing_fields,
                "captured_fields": [],
                "qualification_score": 0,
                "next_action": decision.next_action.value,
                "stage": decision.stage.value,
            }
        facts = self._known_facts(db_session, chat.id)
        decision = self.sales_engine.handle("", facts)
        return {
            "session_id": chat.id,
            "lead_id": chat.lead_id,
            "known_facts": decision.known_facts,
            "missing_fields": decision.missing_fields,
            "captured_fields": decision.captured_fields,
            "qualification_score": decision.qualification_score,
            "next_action": decision.next_action.value,
            "stage": decision.stage.value,
        }

    async def _handle_message(
        self,
        *,
        session: Session,
        text: str,
        session_id: str | int | None,
        client_id: str | None,
        source_channel: str,
        external_user_id: str | None,
        metadata: dict | None,
    ) -> ConversationResult:
        clean_text = (text or "").strip()
        if not clean_text:
            raise HTTPException(status_code=400, detail="text is required")

        metadata = metadata or {}
        chat = self._get_or_create_chat_session(
            session=session,
            session_id=session_id,
            client_id=client_id,
            source_channel=source_channel,
            external_user_id=external_user_id,
            external_chat_id=str(metadata.get("external_chat_id") or "") or None,
            force_new=bool(metadata.get("force_new_session")),
        )
        chat_id = self._require_id(chat.id, "chat session")
        chat.last_user_message_at = self._now()
        chat.updated_at = self._now()
        session.add(chat)
        session.commit()

        user_message = self._save_message(session, chat_id, "in", clean_text)
        guard = evaluate_guardrails(clean_text)
        if not guard.allowed:
            reply = self._guardrail_reply(session, chat_id, clean_text, guard)
            if guard.stop_dialogue:
                chat.current_state_code = "blocked"
                self._record_checkpoint(session, chat, "guardrails", "blocked", f"{guard.reason}:{guard.decision_code}")
            bot = self._save_message(
                session,
                chat_id,
                "out",
                reply,
                blocked=True,
                block_reason=f"{guard.reason}:{guard.decision_code}",
                provider="guardrails",
                model="policy-v2",
            )
            chat.last_bot_message_at = bot.created_at
            session.add(chat)
            session.commit()
            return ConversationResult(
                text=reply,
                session_id=chat_id,
                lead_id=chat.lead_id,
                status=chat.current_state_code,
                state=chat.current_state_code,
                stage=SalesStage.IRRELEVANT.value,
                next_action=NextAction.REFUSE_IRRELEVANT.value,
                source_channel=source_channel,
                provider="guardrails",
                model="policy-v2",
            )

        known_before = self._known_facts(session, chat_id)
        current_decision = self.sales_engine.handle("", known_before)
        current_next_action = current_decision.next_action.value
        user_signal = self.signal_service.classify(clean_text, current_next_action)
        extracted_items = self.sales_engine.extractor.extract(
            clean_text,
            current_next_action=current_next_action,
            known_facts=known_before,
            user_signal=user_signal,
        )
        decision = self.sales_engine.handle(
            clean_text,
            known_before,
            current_next_action=current_next_action,
            user_signal=user_signal,
        )
        known_facts = decision.known_facts
        attempts = self._question_attempts(session, chat_id)
        policy = self.dialogue_policy.choose(
            user_message=clean_text,
            current_next_action=current_next_action,
            next_action=decision.next_action.value,
            known_facts=known_facts,
            missing_fields=decision.missing_fields,
            extracted_facts=extracted_items,
            user_signal=user_signal,
            attempts=attempts,
        )

        for key in decision.extracted_fields:
            if policy.should_save_fact and not policy.should_save_fact.get(key, True):
                continue
            self._upsert_fact(session, chat_id, chat.lead_id, key, known_facts.get(key), user_message.id)
        session.commit()

        lead = self._sync_crm(session, chat, decision, source_channel)
        lead_id = self._require_id(lead.id, "lead")
        for key in decision.extracted_fields:
            if policy.should_save_fact and not policy.should_save_fact.get(key, True):
                continue
            self._attach_fact_to_lead(session, chat_id, key, lead_id)
        self._sync_missing_fields(session, chat_id, lead_id, decision.missing_fields, asked_action=decision.next_action.value)
        session.commit()

        rag_context = RAGService(session).retrieve_context(
            query=clean_text,
            intent=decision.intent or decision.next_action.value,
            lead_state=known_facts,
            limit=5,
        )
        if policy.fallback_text:
            text_out = policy.fallback_text
            provider = "template-policy"
            model = "dialogue-policy-v1"
        elif decision.next_action == NextAction.REFUSE_IRRELEVANT:
            text_out = self.validator.fallback(decision.next_action.value, known_facts)
            provider = "fallback"
            model = "none"
        else:
            composed = await self.composer.compose(
                user_message=clean_text,
                source_channel=source_channel,
                stage=decision.stage.value,
                next_action=decision.next_action.value,
                known_facts=known_facts,
                missing_fields=decision.missing_fields,
                captured_fields=decision.captured_fields,
                retrieved_context=rag_context,
            )
            text_out = composed.text
            provider = composed.provider
            model = composed.model

        last_out = self._last_assistant_text(session, chat_id)
        if last_out and text_out.strip() == last_out.strip():
            text_out = f"Зафиксировал без изменений. {policy.reformulated_question or self.validator.fallback(decision.next_action.value, known_facts)}"

        bot_message = self._save_message(session, chat_id, "out", text_out, provider=provider, model=model)
        chat.last_bot_message_at = bot_message.created_at
        chat.lead_id = lead_id
        chat.current_state_code = lead.status_code
        chat.updated_at = self._now()
        session.add(chat)
        session.commit()

        return ConversationResult(
            text=text_out,
            session_id=chat_id,
            lead_id=lead_id,
            status=lead.status_code,
            state=lead.status_code,
            stage=decision.stage.value,
            next_action=decision.next_action.value,
            captured_fields=decision.captured_fields,
            known_facts=known_facts,
            uncertain_facts=decision.uncertain_facts,
            missing_fields=decision.missing_fields,
            qualification_score=decision.qualification_score,
            source_channel=source_channel,
            request_type=self._db_request_type_code(known_facts.get("request_type")),
            provider=provider,
            model=model,
        )

    def _get_or_create_chat_session(
        self,
        *,
        session: Session,
        session_id: str | int | None,
        client_id: str | None,
        source_channel: str,
        external_user_id: str | None,
        external_chat_id: str | None = None,
        force_new: bool = False,
    ) -> ChatSession:
        source = self._source(session, source_channel)
        if not source or not source.id:
            raise HTTPException(status_code=500, detail="Lead source not configured")
        expected_user = (external_user_id or client_id or "").strip()
        expected_source = source.code

        if session_id and not force_new:
            try:
                numeric_session_id = int(session_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="session_id must be numeric")
            existing = session.get(ChatSession, numeric_session_id)
            if existing:
                if expected_user and existing.external_user_id and expected_user != existing.external_user_id:
                    raise HTTPException(status_code=403, detail="Session owner mismatch")
                source_row = session.get(RefLeadSource, existing.source_id) if existing.source_id else None
                if source_row and source_row.code != expected_source:
                    raise HTTPException(status_code=403, detail="Session channel mismatch")
                if not existing.external_user_id and expected_user:
                    existing.external_user_id = expected_user
                existing.updated_at = self._now()
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

        if not session_id and not force_new and expected_user and expected_source == "telegram":
            existing = session.exec(
                select(ChatSession)
                .where(ChatSession.source_id == source.id, ChatSession.external_user_id == expected_user)
                .order_by(ChatSession.updated_at.desc())
            ).first()
            if existing and existing.current_state_code != "blocked":
                return existing

        chat = ChatSession(
            source_id=source.id,
            external_user_id=expected_user or None,
            external_chat_id=external_chat_id or (expected_user if expected_source == "telegram" else None),
            current_state_code="new",
            language_code="ru",
        )
        session.add(chat)
        session.commit()
        session.refresh(chat)
        return chat

    def _sync_crm(self, session: Session, chat: ChatSession, decision, source_channel: str) -> CrmLead:
        chat_id = self._require_id(chat.id, "chat session")
        facts = decision.known_facts
        source_id = self._require_id(chat.source_id, "lead source")
        request_code = self._db_request_type_code(facts.get("request_type"))
        request_type = session.exec(select(RefRequestType).where(RefRequestType.code == request_code)).first()
        if not request_type or not request_type.id:
            request_type = session.exec(select(RefRequestType).where(RefRequestType.code == "general_company_request")).first()
        if not request_type or not request_type.id:
            raise HTTPException(status_code=500, detail="Request type not configured")

        lead = session.get(CrmLead, chat.lead_id) if chat.lead_id else None
        if not lead:
            lead = CrmLead(
                request_type_id=request_type.id,
                source_id=source_id,
                external_channel_session_id=str(chat_id),
                current_stage_id=self._stage_id(session, "new"),
                status_code="draft",
                priority_code="normal",
                summary="",
                next_action=decision.next_action.value,
            )
            session.add(lead)
            session.commit()
            session.refresh(lead)
            chat.lead_id = lead.id
        else:
            lead.request_type_id = request_type.id

        lead.status_code = self._lead_status(decision.qualification_score, decision.stage.value)
        lead.current_stage_id = self._stage_id(session, self._pipeline_stage_code(lead.status_code))
        lead.next_action = decision.next_action.value
        lead.summary = "; ".join(decision.captured_fields[:8])
        lead.updated_at = self._now()
        lead.hot_flag = self._volume_number(facts.get("volume")) >= 1000
        if lead.hot_flag:
            lead.priority_code = "high"
        session.add(lead)
        session.flush()

        lead_id = self._require_id(lead.id, "lead")
        chat.lead_id = lead_id
        chat.request_type_id = request_type.id
        session.add(chat)
        self._sync_item(session, lead_id, facts, request_code)
        self._sync_contact(session, lead_id, facts)
        self._record_checkpoint(
            session,
            chat,
            "v7_qualification",
            decision.stage.value,
            f"score={decision.qualification_score}; missing={','.join(decision.missing_fields) or 'none'}",
        )
        session.commit()
        session.refresh(lead)
        return lead

    def _sync_item(self, session: Session, lead_id: int, facts: dict[str, Any], request_code: str) -> None:
        item = session.exec(select(CrmLeadItem).where(CrmLeadItem.lead_id == lead_id)).first()
        if not item:
            item = CrmLeadItem(lead_id=lead_id, volume_unit="тонна")

        product_id = self._commodity_id(session, facts.get("product"))
        if product_id:
            item.commodity_id = product_id
        volume = self._volume_number(facts.get("volume"))
        if volume:
            item.volume_value = volume
            item.volume_unit = "тонна" if "кг" not in str(facts.get("volume", "")).lower() else "кг"
        region_id = self._region_id(session, facts.get("region"))
        if region_id:
            if request_code == "sale_to_buyer":
                item.destination_region_id = region_id
            else:
                item.source_region_id = region_id
        if facts.get("quality_class"):
            item.freeform_quality_text = str(facts["quality_class"])
        comments = []
        for key in ["route_from", "route_to", "transport_type", "delivery_terms", "timing", "comment"]:
            if facts.get(key):
                comments.append(f"{key}: {facts[key]}")
        item.comment = "; ".join(comments)[:1000]
        session.add(item)

    def _sync_contact(self, session: Session, lead_id: int, facts: dict[str, Any]) -> None:
        row = session.exec(select(CrmLeadContactSnapshot).where(CrmLeadContactSnapshot.lead_id == lead_id)).first()
        if not row:
            row = CrmLeadContactSnapshot(lead_id=lead_id)
        contact = str(facts.get("contact") or "").strip()
        if contact:
            if contact.startswith("@"):
                row.telegram = contact
            elif "@" in contact:
                row.email = contact
            else:
                row.phone = contact
        if facts.get("company_name"):
            row.company_name = str(facts["company_name"])
        session.add(row)

    def _sync_missing_fields(self, session: Session, chat_id: int, lead_id: int, missing: list[str], asked_action: str | None = None) -> None:
        existing = session.exec(select(ChatMissingField).where(ChatMissingField.session_id == chat_id)).all()
        by_code = {row.field_code: row for row in existing}
        missing_set = set(missing)
        asked_field = self._field_for_action(asked_action or "")
        for order, field in enumerate(REQUIRED_FIELDS, start=1):
            row = by_code.get(field)
            if not row:
                row = ChatMissingField(session_id=chat_id, lead_id=lead_id, field_code=field, priority_order=order, is_required=True)
            row.lead_id = lead_id
            row.is_collected = field not in missing_set
            if row.is_collected and not row.resolved_at:
                row.resolved_at = self._now()
            if not row.is_collected:
                row.resolved_at = None
            if field == asked_field and field in missing_set:
                row.asked_count = (row.asked_count or 0) + 1
                row.last_asked_at = self._now()
            session.add(row)

    @staticmethod
    def _field_for_action(action: str) -> str:
        return {
            NextAction.ASK_REQUEST_TYPE.value: "request_type",
            NextAction.ASK_PRODUCT.value: "product",
            NextAction.ASK_VOLUME.value: "volume",
            NextAction.ASK_REGION.value: "region",
            NextAction.ASK_TIMING.value: "timing",
            NextAction.ASK_CONTACT.value: "contact",
        }.get(action, "")

    def _question_attempts(self, session: Session, chat_id: int) -> dict[str, int]:
        rows = session.exec(select(ChatMissingField).where(ChatMissingField.session_id == chat_id)).all()
        return {row.field_code: row.asked_count or 0 for row in rows}

    def _known_facts(self, session: Session, chat_id: int) -> dict[str, Any]:
        rows = session.exec(select(ChatExtractedFact).where(ChatExtractedFact.session_id == chat_id)).all()
        known: dict[str, Any] = {}
        for row in rows:
            if row.fact_key not in set(REQUIRED_FIELDS + ["quality_class", "delivery_terms", "company_name", "route_from", "route_to", "transport_type", "comment"]):
                continue
            value: Any = row.fact_value_text
            if not value and row.fact_value_numeric is not None:
                value = row.fact_value_numeric
            if value not in ("", None):
                known[row.fact_key] = value
        return self.qualifier.normalize_known_facts(known)

    def _upsert_fact(self, session: Session, chat_id: int, lead_id: int | None, key: str, value: Any, message_id: int | None) -> None:
        if value in ("", None):
            return
        text_value = str(value)
        numeric = self._volume_number(value) if key == "volume" else None
        row = session.exec(
            select(ChatExtractedFact).where(ChatExtractedFact.session_id == chat_id, ChatExtractedFact.fact_key == key)
        ).first()
        if row:
            row.fact_value_text = text_value
            row.fact_value_numeric = numeric
            row.confidence = max(row.confidence, 0.9)
            row.source_message_id = message_id
            row.lead_id = lead_id
            row.updated_at = self._now()
        else:
            row = ChatExtractedFact(
                session_id=chat_id,
                lead_id=lead_id,
                fact_key=key,
                fact_value_text=text_value,
                fact_value_numeric=numeric,
                confidence=0.9,
                source_message_id=message_id,
                is_confirmed=True,
            )
        session.add(row)

    def _attach_fact_to_lead(self, session: Session, chat_id: int, key: str, lead_id: int) -> None:
        row = session.exec(
            select(ChatExtractedFact).where(ChatExtractedFact.session_id == chat_id, ChatExtractedFact.fact_key == key)
        ).first()
        if row:
            row.lead_id = lead_id
            session.add(row)

    @staticmethod
    def _guardrail_reply(session: Session, chat_id: int, text: str, guard) -> str:
        previous_out = sorted(
            session.exec(select(ChatMessage).where(ChatMessage.session_id == chat_id, ChatMessage.direction == "out")).all(),
            key=lambda x: x.created_at,
        )
        return render_guardrail_reply(guard, user_text=text, last_assistant_messages=[row.text for row in previous_out[-3:]])

    @staticmethod
    def _last_assistant_text(session: Session, chat_id: int) -> str:
        previous_out = sorted(
            session.exec(select(ChatMessage).where(ChatMessage.session_id == chat_id, ChatMessage.direction == "out")).all(),
            key=lambda x: x.created_at,
        )
        return previous_out[-1].text if previous_out else ""

    @staticmethod
    def _save_message(
        session: Session,
        session_id: int,
        direction: str,
        text: str,
        message_type: str = "text",
        blocked: bool = False,
        block_reason: str = "",
        provider: str | None = None,
        model: str | None = None,
    ) -> ChatMessage:
        row = ChatMessage(
            session_id=session_id,
            direction=direction,
            text=text,
            message_type=message_type,
            blocked=blocked,
            block_reason=block_reason,
            llm_provider=provider,
            llm_model=model,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    def _source(self, session: Session, channel: str) -> RefLeadSource | None:
        code = self._source_code(channel)
        row = session.exec(select(RefLeadSource).where(RefLeadSource.code == code)).first()
        if row:
            return row
        return session.exec(select(RefLeadSource)).first()

    @staticmethod
    def _source_code(channel: str) -> str:
        normalized = (channel or "").strip().lower()
        if "telegram" in normalized:
            return "telegram"
        if "phone" in normalized:
            return "phone"
        if "crm" in normalized:
            return "crm_import"
        return "web_widget"

    @staticmethod
    def _db_request_type_code(v7_request_type: Any) -> str:
        return DB_REQUEST_TYPE_BY_V7.get(str(v7_request_type or ""), "general_company_request")

    @staticmethod
    def _lead_status(score: int, stage: str) -> str:
        if stage == SalesStage.HANDED_TO_MANAGER.value:
            return "handed_to_manager"
        if score >= 100:
            return "qualified"
        if score >= 50:
            return "partially_qualified"
        return "draft"

    @staticmethod
    def _pipeline_stage_code(status: str) -> str:
        if status == "qualified":
            return "qualified"
        if status == "handed_to_manager":
            return "handed_to_manager"
        if status == "partially_qualified":
            return "partially_qualified"
        return "draft"

    def _stage_id(self, session: Session, code: str) -> int:
        row = session.exec(select(RefPipelineStage).where(RefPipelineStage.code == code)).first()
        if row and row.id:
            return row.id
        fallback = session.exec(select(RefPipelineStage)).first()
        if not fallback or not fallback.id:
            raise HTTPException(status_code=500, detail="RefPipelineStage not seeded")
        return fallback.id

    @staticmethod
    def _commodity_id(session: Session, product: Any) -> int | None:
        value = str(product or "").strip().lower().replace("ё", "е")
        if not value:
            return None
        rows = session.exec(select(RefCommodity).where(RefCommodity.is_active == True)).all()
        for row in rows:
            names = [row.code, row.name, row.full_name]
            if any(value in (name or "").lower().replace("ё", "е") or (name or "").lower().replace("ё", "е") in value for name in names if name):
                return row.id
        return None

    @staticmethod
    def _region_id(session: Session, region: Any) -> int | None:
        value = str(region or "").strip().lower().replace("ё", "е")
        if not value or "->" in value or value == "порт":
            return None
        rows = session.exec(select(RefRegion).where(RefRegion.is_active == True)).all()
        for row in rows:
            names = [row.code, row.region_name, row.city_name, row.port_name]
            for name in names:
                lowered = (name or "").lower().replace("ё", "е")
                if lowered and (lowered in value or value in lowered):
                    return row.id
        return None

    @staticmethod
    def _volume_number(volume: Any) -> float:
        m = re.search(r"(\d+(?:[.,]\d+)?)", str(volume or ""))
        if not m:
            return 0.0
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return 0.0

    @staticmethod
    def _record_checkpoint(session: Session, chat: ChatSession, code: str, status: str, note: str) -> None:
        if not chat.id:
            return
        session.add(
            ChatQualificationCheckpoint(
                session_id=chat.id,
                lead_id=chat.lead_id,
                checkpoint_code=code,
                checkpoint_status=status,
                note=note,
            )
        )

    @staticmethod
    def _require_id(value: int | None, entity: str) -> int:
        if value is None:
            raise HTTPException(status_code=500, detail=f"{entity} id is not initialized")
        return value

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)
