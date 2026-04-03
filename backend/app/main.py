import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .agent import SalesAssistantAgent
from .db import engine, get_session, init_db
from .guardrails import evaluate_guardrails
from .llm_service import LLMService, LLMUnavailableError
from .models import (
    AdminSetting,
    AdminUser,
    CatalogPricePolicy,
    CatalogQualityTemplate,
    CatalogQualityTemplateLine,
    CatalogStockPlaceholder,
    ChatExtractedFact,
    ChatMessage,
    ChatMissingField,
    ChatQualificationCheckpoint,
    ChatSession,
    CompanyProfile,
    CrmCounterparty,
    CrmLead,
    CrmLeadContactSnapshot,
    CrmLeadItem,
    CrmTask,
    KnowledgeArticle,
    RefCommodity,
    RefCounterpartyType,
    RefDeliveryBasis,
    RefDepartment,
    RefLeadSource,
    RefManagerRole,
    RefPipelineStage,
    RefRegion,
    RefRequestType,
    RefTransportMode,
)
from .sales_logic import (
    detect_request_type,
    extract_facts,
    human_field_name,
    minimum_viable_application,
    next_missing_field,
    next_question_for,
    required_fields,
)
from .seed import seed_defaults

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("agrolead.api")

app = FastAPI(title="AgroLead Assistant API", version="5.0.0")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "315920")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "agrolead-admin-token")

llm_service = LLMService()
agent = SalesAssistantAgent(llm_service=llm_service)


class LoginIn(BaseModel):
    username: str
    password: str


class ChatIn(BaseModel):
    text: str
    session_id: Optional[int] = None
    client_id: str = "web"
    source_channel: str = "web_widget"
    external_user_id: Optional[str] = None
    external_chat_id: Optional[str] = None


class ChatDryRunIn(BaseModel):
    text: str


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_id(value: Optional[int], entity: str) -> int:
    if value is None:
        raise HTTPException(status_code=500, detail=f"{entity} id is not initialized")
    return value


def _mask_settings(items: list[AdminSetting]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        payload = item.model_dump()
        if item.is_secret:
            payload["setting_value"] = "***"
        out.append(payload)
    return out


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _source_code_from_channel(channel: str) -> str:
    normalized = (channel or "").strip().lower()
    if "telegram" in normalized:
        return "telegram"
    if "crm" in normalized:
        return "crm_import"
    if "phone" in normalized:
        return "phone"
    return "web_widget"


def _is_faq_like(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return any(
        marker in normalized
        for marker in [
            "кто вы",
            "чем занимает",
            "какие услуги",
            "контакты",
            "где находитесь",
            "реквизиты",
        ]
    )


def _get_ref_by_code(session: Session, model, code: str):
    return session.exec(select(model).where(model.code == code)).first()


def _stage_id(session: Session, code: str) -> int:
    row = _get_ref_by_code(session, RefPipelineStage, code)
    if row:
        return _require_id(row.id, "pipeline stage")
    fallback = session.exec(select(RefPipelineStage)).first()
    if not fallback:
        raise HTTPException(status_code=500, detail="RefPipelineStage not seeded")
    return _require_id(fallback.id, "pipeline stage")


def _request_type_name(session: Session, request_type_id: Optional[int]) -> str:
    if not request_type_id:
        return "Общий запрос"
    row = session.get(RefRequestType, request_type_id)
    return row.name if row else "Общий запрос"


def _request_type_code(session: Session, request_type_id: Optional[int]) -> str:
    if not request_type_id:
        return "general_company_request"
    row = session.get(RefRequestType, request_type_id)
    return row.code if row else "general_company_request"


def _get_or_create_chat_session(session: Session, payload: ChatIn) -> ChatSession:
    if payload.session_id:
        existing = session.get(ChatSession, payload.session_id)
        if existing:
            existing.updated_at = _now()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    source_code = _source_code_from_channel(payload.source_channel)
    source = _get_ref_by_code(session, RefLeadSource, source_code)
    if not source:
        source = session.exec(select(RefLeadSource)).first()
    if not source:
        raise HTTPException(status_code=500, detail="Lead source not configured")

    chat = ChatSession(
        source_id=_require_id(source.id, "lead source"),
        external_user_id=payload.external_user_id or payload.client_id,
        external_chat_id=payload.external_chat_id,
        current_state_code="new",
        language_code="ru",
    )
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return chat


def _save_message(
    session: Session,
    session_id: int,
    direction: str,
    text: str,
    message_type: str = "text",
    blocked: bool = False,
    block_reason: str = "",
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> ChatMessage:
    message = ChatMessage(
        session_id=session_id,
        direction=direction,
        text=text,
        message_type=message_type,
        blocked=blocked,
        block_reason=block_reason,
        llm_provider=provider,
        llm_model=model,
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def _upsert_fact(
    session: Session,
    chat: ChatSession,
    lead_id: Optional[int],
    key: str,
    text_value: str,
    numeric_value: Optional[float],
    confidence: float,
    source_message_id: Optional[int],
) -> None:
    chat_id = _require_id(chat.id, "chat session")
    row = session.exec(
        select(ChatExtractedFact).where(
            ChatExtractedFact.session_id == chat_id,
            ChatExtractedFact.fact_key == key,
        )
    ).first()
    if row:
        row.fact_value_text = text_value
        row.fact_value_numeric = numeric_value
        row.confidence = max(row.confidence, confidence)
        row.source_message_id = source_message_id
        row.lead_id = lead_id
        row.updated_at = _now()
        session.add(row)
    else:
        session.add(
            ChatExtractedFact(
                session_id=chat_id,
                lead_id=lead_id,
                fact_key=key,
                fact_value_text=text_value,
                fact_value_numeric=numeric_value,
                confidence=confidence,
                source_message_id=source_message_id,
                is_confirmed=confidence >= 0.8,
            )
        )


def _facts_map(session: Session, chat_id: int) -> dict[str, ChatExtractedFact]:
    rows = session.exec(select(ChatExtractedFact).where(ChatExtractedFact.session_id == chat_id)).all()
    return {row.fact_key: row for row in rows}


def _ensure_missing_fields(session: Session, chat: ChatSession, request_code: str, lead_id: Optional[int]) -> None:
    chat_id = _require_id(chat.id, "chat session")
    existing = session.exec(select(ChatMissingField).where(ChatMissingField.session_id == chat_id)).all()
    existing_map = {item.field_code: item for item in existing}
    for order, field_code in enumerate(required_fields(request_code), start=1):
        if field_code in existing_map:
            continue
        session.add(
            ChatMissingField(
                session_id=chat_id,
                lead_id=lead_id,
                field_code=field_code,
                priority_order=order,
                is_required=True,
                is_collected=False,
            )
        )


def _update_missing_fields(session: Session, chat: ChatSession, fact_keys: set[str]) -> None:
    chat_id = _require_id(chat.id, "chat session")
    rows = session.exec(select(ChatMissingField).where(ChatMissingField.session_id == chat_id)).all()
    for row in rows:
        collected = row.field_code in fact_keys
        if collected and not row.is_collected:
            row.is_collected = True
            row.resolved_at = _now()
        row.lead_id = chat.lead_id
        session.add(row)


def _compact_summary(facts: dict[str, ChatExtractedFact]) -> str:
    preferred = [
        "commodity_id",
        "requested_volume_value",
        "volume_value",
        "source_region_id",
        "destination_region_id_or_port",
        "transport_mode_id",
        "contact_phone_or_telegram_or_email",
    ]
    lines: list[str] = []
    for key in preferred:
        item = facts.get(key)
        if not item or not item.fact_value_text:
            continue
        lines.append(f"{human_field_name(key)}: {item.fact_value_text}")
    return "; ".join(lines)


def _summary_lines(facts: dict[str, ChatExtractedFact]) -> list[str]:
    rows = []
    for key, item in facts.items():
        if not item.fact_value_text:
            continue
        rows.append(f"{human_field_name(key)}: {item.fact_value_text}")
    rows.sort()
    return rows[:8]


def _counterparty_type_code_by_request(request_code: str) -> str:
    mapping = {
        "purchase_from_supplier": "supplier",
        "sale_to_buyer": "buyer",
        "logistics_request": "carrier",
        "storage_request": "terminal",
        "export_request": "buyer",
    }
    return mapping.get(request_code, "buyer")


def _is_company_label(label: str) -> bool:
    normalized = (label or "").strip().lower()
    if not normalized:
        return False
    if any(normalized.startswith(prefix) for prefix in ["ооо", "ао", "пао", "зао", "оао", "ип", "кфх"]):
        return True
    return any(marker in normalized for marker in ["компания", "холдинг", "трейд", "агро", "зерно", "логист"])


def _sync_counterparty_from_contact(
    session: Session,
    lead: CrmLead,
    request_code: str,
    contact_row: CrmLeadContactSnapshot,
) -> None:
    phone = (contact_row.phone or "").strip()
    email = (contact_row.email or "").strip().lower()
    telegram = (contact_row.telegram or "").strip().lower()
    company_name = (contact_row.company_name or "").strip()
    contact_name = (contact_row.contact_name or "").strip()

    if not (phone or email or telegram):
        return
    if not (company_name or contact_name):
        return

    rows = session.exec(select(CrmCounterparty)).all()
    existing = None
    for row in rows:
        if phone and (row.phone or "").strip() == phone:
            existing = row
            break
        if email and (row.email or "").strip().lower() == email:
            existing = row
            break
        if telegram and (row.telegram or "").strip().lower() == telegram:
            existing = row
            break
        if company_name and (row.company_name or "").strip().lower() == company_name.lower():
            existing = row
            break

    if existing is None:
        type_code = _counterparty_type_code_by_request(request_code)
        type_row = _get_ref_by_code(session, RefCounterpartyType, type_code)
        if not type_row:
            type_row = session.exec(select(RefCounterpartyType)).first()
        if not type_row:
            return

        existing = CrmCounterparty(counterparty_type_id=_require_id(type_row.id, "counterparty type"))

    if company_name:
        existing.company_name = company_name
    if contact_name:
        existing.contact_person = contact_name
    if phone:
        existing.phone = phone
    if email:
        existing.email = email
    if telegram:
        existing.telegram = telegram
    existing.updated_at = _now()

    session.add(existing)
    session.flush()
    if existing.id:
        lead.counterparty_id = existing.id


def _sync_lead_tables(session: Session, chat: ChatSession, request_code: str, source_id: int) -> CrmLead:
    chat_id = _require_id(chat.id, "chat session")
    lead = session.get(CrmLead, chat.lead_id) if chat.lead_id else None
    request_type = _get_ref_by_code(session, RefRequestType, request_code)
    if not request_type:
        request_type = _get_ref_by_code(session, RefRequestType, "general_company_request")
    if not request_type:
        raise HTTPException(status_code=500, detail="Request type not configured")
    request_type_id = _require_id(request_type.id, "request type")

    if not lead:
        lead = CrmLead(
            request_type_id=request_type_id,
            source_id=source_id,
            external_channel_session_id=str(chat_id),
            current_stage_id=_stage_id(session, "new"),
            status_code="draft",
            priority_code="normal",
            summary="",
            next_action="Собрать недостающие поля заявки",
        )
        session.add(lead)
        session.commit()
        session.refresh(lead)
        chat.lead_id = _require_id(lead.id, "lead")
        chat.request_type_id = request_type_id
        session.add(chat)
        session.commit()
    elif lead.request_type_id != request_type_id:
        lead.request_type_id = request_type_id
        lead.updated_at = _now()
        chat.request_type_id = request_type_id
        session.add(lead)
        session.add(chat)
        session.commit()

    lead_id = _require_id(lead.id, "lead")

    facts = _facts_map(session, chat_id)

    # lead item
    item = session.exec(select(CrmLeadItem).where(CrmLeadItem.lead_id == lead_id)).first()
    if not item:
        item = CrmLeadItem(lead_id=lead_id, volume_unit="тонна")

    def _fact_text(key: str) -> str:
        row = facts.get(key)
        return row.fact_value_text if row else ""

    def _fact_num(key: str) -> Optional[float]:
        row = facts.get(key)
        return row.fact_value_numeric if row else None

    commodity_num = _fact_num("commodity_id")
    if commodity_num is not None:
        item.commodity_id = int(commodity_num)

    volume_value = _fact_num("requested_volume_value") or _fact_num("volume_value")
    if volume_value is not None:
        item.volume_value = float(volume_value)

    volume_unit = _fact_text("requested_volume_unit") or _fact_text("volume_unit")
    if volume_unit:
        item.volume_unit = volume_unit

    source_region = _fact_num("source_region_id")
    if source_region is not None:
        item.source_region_id = int(source_region)

    destination_region = _fact_num("destination_region_id_or_port")
    if destination_region is not None:
        item.destination_region_id = int(destination_region)

    transport_mode = _fact_num("transport_mode_id")
    if transport_mode is not None:
        item.transport_mode_id = int(transport_mode)

    delivery_basis = _fact_num("delivery_basis_id")
    if delivery_basis is not None:
        item.delivery_basis_id = int(delivery_basis)

    quality_text = _fact_text("quality_profile_text") or _fact_text("requested_quality_text")
    if quality_text:
        item.freeform_quality_text = quality_text

    target_price = _fact_num("target_price")
    if target_price is not None:
        item.target_price = target_price

    export_flag = _fact_num("export_flag")
    if export_flag is not None:
        item.export_flag = int(export_flag) == 1

    item.comment = _fact_text("comment") or item.comment
    session.add(item)

    # contact snapshot
    contact_row = session.exec(select(CrmLeadContactSnapshot).where(CrmLeadContactSnapshot.lead_id == lead_id)).first()
    if not contact_row:
        contact_row = CrmLeadContactSnapshot(lead_id=lead_id)
    contact = _fact_text("contact_phone_or_telegram_or_email")
    if contact:
        if contact.startswith("@"):
            contact_row.telegram = contact
        elif "@" in contact:
            contact_row.email = contact
        else:
            contact_row.phone = contact
    who = _fact_text("contact_name_or_company")
    if who:
        if _is_company_label(who):
            contact_row.company_name = who
            if not contact_row.contact_name:
                contact_row.contact_name = ""
        else:
            contact_row.contact_name = who
            if not contact_row.company_name:
                contact_row.company_name = ""
    session.add(contact_row)

    _sync_counterparty_from_contact(session, lead, request_code, contact_row)

    fact_keys = set(facts.keys())
    has_contact = bool(_fact_text("contact_phone_or_telegram_or_email"))
    min_viable = minimum_viable_application(request_code, fact_keys=fact_keys, has_contact=has_contact)

    required = required_fields(request_code)
    collected = {key for key in fact_keys if key in required}
    is_qualified = len(required) > 0 and all(field in collected for field in required)

    lead.summary = _compact_summary(facts)
    lead.updated_at = _now()

    if is_qualified:
        lead.status_code = "qualified"
        lead.current_stage_id = _stage_id(session, "qualified")
        lead.next_action = "Передать менеджеру и зафиксировать коммерческое предложение"
    elif min_viable:
        lead.status_code = "partially_qualified"
        lead.current_stage_id = _stage_id(session, "partially_qualified")
        lead.next_action = "Дособрать критичные поля и назначить менеджера"
    else:
        lead.status_code = "draft"
        lead.current_stage_id = _stage_id(session, "draft")
        lead.next_action = "Собрать минимальный набор полей"

    high_volume = (item.volume_value or 0) >= 1000
    high_urgency = (_fact_text("urgency") or "") == "high"
    lead.hot_flag = high_volume or high_urgency
    if lead.hot_flag:
        lead.priority_code = "high"

    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def _resolve_code_facts(session: Session, facts: dict[str, Any]) -> None:
    transport = facts.get("transport_mode_code")
    if transport and transport.text:
        row = _get_ref_by_code(session, RefTransportMode, transport.text)
        if row:
            facts["transport_mode_id"] = type(transport)(text=str(row.id), numeric=float(row.id), confidence=transport.confidence)

    basis = facts.get("delivery_basis_code")
    if basis and basis.text:
        row = _get_ref_by_code(session, RefDeliveryBasis, basis.text)
        if row:
            facts["delivery_basis_id"] = type(basis)(text=str(row.id), numeric=float(row.id), confidence=basis.confidence)


def _resolve_maps(session: Session) -> tuple[dict[str, int], dict[str, int]]:
    commodities = session.exec(select(RefCommodity).where(RefCommodity.is_active == True)).all()
    commodity_map: dict[str, int] = {}
    for row in commodities:
        commodity_id = _require_id(row.id, "commodity")
        commodity_map[row.name.lower()] = commodity_id
        if row.full_name:
            commodity_map[row.full_name.lower()] = commodity_id

    regions = session.exec(select(RefRegion).where(RefRegion.is_active == True)).all()
    region_map: dict[str, int] = {}
    for row in regions:
        region_id = _require_id(row.id, "region")
        if row.region_name:
            region_map[row.region_name.lower()] = region_id
        if row.city_name:
            region_map[row.city_name.lower()] = region_id
        if row.port_name:
            region_map[row.port_name.lower()] = region_id
    return commodity_map, region_map


def _record_checkpoint(session: Session, chat: ChatSession, code: str, status: str, note: str) -> None:
    chat_id = _require_id(chat.id, "chat session")
    session.add(
        ChatQualificationCheckpoint(
            session_id=chat_id,
            lead_id=chat.lead_id,
            checkpoint_code=code,
            checkpoint_status=status,
            note=note,
        )
    )


async def _process_chat(session: Session, payload: ChatIn) -> dict[str, Any]:
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    chat = _get_or_create_chat_session(session, payload)
    chat_id = _require_id(chat.id, "chat session")
    chat.last_user_message_at = _now()
    chat.updated_at = _now()
    session.add(chat)
    session.commit()

    user_message = _save_message(session, chat_id, direction="in", text=text)

    guard = evaluate_guardrails(text)
    if not guard.allowed:
        if guard.stop_dialogue:
            chat.current_state_code = "blocked"
            session.add(chat)
            _record_checkpoint(session, chat, "guardrails", "blocked", guard.reason)
            session.commit()
        reply = guard.answer
        bot_message = _save_message(
            session,
            chat_id,
            direction="out",
            text=reply,
            blocked=True,
            block_reason=guard.reason,
            provider="guardrails",
            model="rule-based",
        )
        chat.last_bot_message_at = bot_message.created_at
        session.add(chat)
        session.commit()
        return {
            "session_id": chat_id,
            "lead_id": chat.lead_id,
            "text": reply,
            "provider": "guardrails",
            "model": "rule-based",
            "state": chat.current_state_code,
            "done": True,
        }

    request_code = _request_type_code(session, chat.request_type_id)
    if request_code == "general_company_request":
        request_code = detect_request_type(text)

    request_type = _get_ref_by_code(session, RefRequestType, request_code)
    if not request_type:
        request_type = _get_ref_by_code(session, RefRequestType, "general_company_request")
    if not request_type:
        raise HTTPException(status_code=500, detail="Request type not configured")
    chat.request_type_id = _require_id(request_type.id, "request type")
    source_id = _require_id(chat.source_id, "lead source")

    lead = _sync_lead_tables(session, chat, request_code=request_type.code, source_id=source_id)
    lead_id = _require_id(lead.id, "lead")
    chat.lead_id = lead_id

    commodity_map, region_map = _resolve_maps(session)
    extracted = extract_facts(text=text, commodity_by_name=commodity_map, region_by_name=region_map)
    _resolve_code_facts(session, extracted)

    request_hint = extracted.get("request_type_hint")
    if request_type.code == "general_company_request" and request_hint and request_hint.text:
        hinted = _get_ref_by_code(session, RefRequestType, request_hint.text)
        if hinted:
            request_type = hinted
            chat.request_type_id = _require_id(request_type.id, "request type")

    _ensure_missing_fields(session, chat, request_type.code, lead_id)

    for key, value in extracted.items():
        _upsert_fact(
            session=session,
            chat=chat,
            lead_id=lead_id,
            key=key,
            text_value=value.text,
            numeric_value=value.numeric,
            confidence=value.confidence,
            source_message_id=user_message.id,
        )

    session.commit()

    lead = _sync_lead_tables(session, chat, request_code=request_type.code, source_id=source_id)
    lead_id = _require_id(lead.id, "lead")
    facts = _facts_map(session, chat_id)
    fact_keys = set(facts.keys())
    _update_missing_fields(session, chat, fact_keys)

    required = required_fields(request_type.code)
    missing = next_missing_field(required, fact_keys)
    next_question = ""
    stage = "draft"
    if lead.status_code == "qualified":
        stage = "qualified"
    elif lead.status_code == "partially_qualified":
        stage = "partially_qualified"
    elif request_type.code == "general_company_request" and _is_faq_like(text):
        stage = "faq"
    elif not fact_keys:
        stage = "new"

    if lead.status_code == "qualified":
        next_question = "Заявка зафиксирована. Менеджер продолжит работу по условиям и фиксации сделки."
        _record_checkpoint(session, chat, "qualification", "qualified", "Все обязательные поля собраны")
    else:
        next_question = next_question_for(missing) if missing else "Уточните приоритетный параметр сделки."
        _record_checkpoint(session, chat, "qualification", "in_progress", f"missing={missing or 'none'}")

    # последние ответы ассистента для anti-repeat
    out_rows = sorted(
        session.exec(select(ChatMessage).where(ChatMessage.session_id == chat_id, ChatMessage.direction == "out")).all(),
        key=lambda x: x.created_at,
    )
    last_assistant = [row.text for row in out_rows[-3:]]

    reply = await agent.reply(
        stage=stage,
        request_type_name=request_type.name,
        user_text=text,
        summary_lines=_summary_lines(facts),
        next_question=next_question,
        last_assistant_messages=last_assistant,
    )

    bot_message = _save_message(
        session,
        chat_id,
        direction="out",
        text=reply.text,
        provider=reply.provider,
        model=reply.model,
    )

    chat.last_bot_message_at = bot_message.created_at
    chat.current_state_code = lead.status_code
    chat.updated_at = _now()
    session.add(chat)
    session.commit()

    return {
        "session_id": chat_id,
        "lead_id": lead_id,
        "request_type": request_type.code,
        "status": lead.status_code,
        "state": chat.current_state_code,
        "provider": reply.provider,
        "model": reply.model,
        "text": reply.text,
        "done": True,
    }


@app.on_event("startup")
def startup() -> None:
    init_db()
    with Session(engine) as session:
        seed_defaults(session)
    LOGGER.info("Startup completed")


@app.on_event("shutdown")
async def shutdown() -> None:
    await llm_service.close()
    LOGGER.info("Shutdown completed")


@app.get("/api/health")
def health(session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        session.exec(select(RefCommodity)).first()
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "error",
        "time": _now().isoformat(),
        "agent_engine": "sales-lead-orchestrator-v5",
        "db_ok": db_ok,
    }


@app.get("/api/llm/status")
def llm_status() -> dict[str, Any]:
    return llm_service.status()


@app.post("/api/v1/chat")
async def chat_v1(payload: ChatIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    return await _process_chat(session, payload)


@app.post("/api/chat")
async def chat_compat(payload: ChatIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    return await _process_chat(session, payload)


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatIn, session: Session = Depends(get_session)) -> StreamingResponse:
    result = await _process_chat(session, payload)

    async def generator():
        text = result.get("text", "") or ""
        for ch in text:
            yield json.dumps({"session_id": result["session_id"], "token": ch, "done": False}, ensure_ascii=False) + "\n"
            await asyncio.sleep(0)

        yield json.dumps(
            {
                "session_id": result["session_id"],
                "lead_id": result.get("lead_id"),
                "request_type": result.get("request_type"),
                "status": result.get("status"),
                "provider": result.get("provider"),
                "model": result.get("model"),
                "token": text,
                "done": True,
            },
            ensure_ascii=False,
        ) + "\n"

    return StreamingResponse(generator(), media_type="application/x-ndjson")


@app.post("/api/chat/dry-run")
async def chat_dry_run(payload: ChatDryRunIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    guard = evaluate_guardrails(text)
    if not guard.allowed:
        return {"done": True, "provider": "guardrails", "model": "rule-based", "text": guard.answer}

    try:
        request_code = detect_request_type(text)
        request = _get_ref_by_code(session, RefRequestType, request_code)
        commodity_map, region_map = _resolve_maps(session)
        facts = extract_facts(text, commodity_map, region_map)
        _resolve_code_facts(session, facts)
        required = required_fields(request_code)
        missing = next_missing_field(required, set(facts.keys()))
        question = next_question_for(missing) if missing else "Если готовы, фиксирую заявку и передаю менеджеру."
        summary = [f"{human_field_name(k)}: {v.text}" for k, v in facts.items() if v.text][:8]
        reply = await agent.reply(
            stage="draft",
            request_type_name=request.name if request else request_code,
            user_text=text,
            summary_lines=summary,
            next_question=question,
            last_assistant_messages=[],
        )
        if reply.provider == "service-unavailable":
            detail = llm_service.last_error or "LLM unavailable"
            raise HTTPException(status_code=503, detail=detail)
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"done": True, "provider": reply.provider, "model": reply.model, "text": reply.text}


@app.get("/api/public/bootstrap")
def bootstrap(session: Session = Depends(get_session)) -> dict[str, Any]:
    company = session.exec(select(CompanyProfile)).first()
    commodities = session.exec(select(RefCommodity).where(RefCommodity.is_active == True)).all()
    request_types = session.exec(select(RefRequestType).where(RefRequestType.is_active == True)).all()
    return {
        "company": company,
        "commodities": commodities,
        "request_types": request_types,
        "llm": llm_service.status(),
    }


@app.post("/api/admin/login")
@app.post("/api/v1/admin/login")
def admin_login(payload: LoginIn, session: Session = Depends(get_session)) -> dict[str, str]:
    user = session.exec(select(AdminUser).where(AdminUser.login == payload.username, AdminUser.is_active == True)).first()
    fallback_ok = payload.username == ADMIN_USER and payload.password == ADMIN_PASS
    if user:
        if user.password_hash != _hash_password(payload.password):
            raise HTTPException(status_code=401, detail="Bad credentials")
    elif not fallback_ok:
        raise HTTPException(status_code=401, detail="Bad credentials")
    return {"token": ADMIN_TOKEN}


@app.get("/api/v1/admin/stats")
@app.get("/api/admin/stats")
def admin_stats(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, Any]:
    leads = session.exec(select(CrmLead)).all()
    sessions = session.exec(select(ChatSession)).all()
    tasks = session.exec(select(CrmTask)).all()
    request_types = {row.id: row.code for row in session.exec(select(RefRequestType)).all()}

    by_request: dict[str, int] = {}
    for lead in leads:
        code = request_types.get(lead.request_type_id, "unknown")
        by_request[code] = by_request.get(code, 0) + 1

    stage_counts: dict[str, int] = {}
    for lead in leads:
        stage_counts[lead.status_code] = stage_counts.get(lead.status_code, 0) + 1

    return {
        "leads_total": len(leads),
        "hot_leads": len([lead for lead in leads if lead.hot_flag]),
        "unassigned_leads": len([lead for lead in leads if not lead.assigned_manager_user_id]),
        "sessions_total": len(sessions),
        "tasks_open": len([task for task in tasks if task.status != "done"]),
        "by_request_type": by_request,
        "by_stage": stage_counts,
    }


@app.get("/api/v1/admin/pipeline")
def admin_pipeline(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, Any]:
    stages = session.exec(select(RefPipelineStage).where(RefPipelineStage.is_active == True)).all()
    leads = session.exec(select(CrmLead)).all()
    counters = {}
    for lead in leads:
        counters[lead.status_code] = counters.get(lead.status_code, 0) + 1
    items = []
    for stage in sorted(stages, key=lambda x: x.sort_order):
        items.append({"code": stage.code, "name": stage.name, "count": counters.get(stage.code, 0)})
    return {"items": items}


@app.get("/api/v1/leads")
@app.get("/api/admin/leads")
def list_leads(
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
    status_code: Optional[str] = None,
    request_type_code: Optional[str] = None,
    hot_only: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    leads = session.exec(select(CrmLead)).all()
    request_type_by_id = {row.id: row.code for row in session.exec(select(RefRequestType)).all()}

    if status_code:
        leads = [lead for lead in leads if lead.status_code == status_code]
    if request_type_code:
        leads = [lead for lead in leads if request_type_by_id.get(lead.request_type_id) == request_type_code]
    if hot_only:
        leads = [lead for lead in leads if lead.hot_flag]

    leads = sorted(leads, key=lambda x: x.updated_at, reverse=True)
    result = []
    for lead in leads[:limit]:
        item = lead.model_dump()
        item["request_type_code"] = request_type_by_id.get(lead.request_type_id, "unknown")
        lead_item = session.exec(select(CrmLeadItem).where(CrmLeadItem.lead_id == lead.id)).first()
        snapshot = session.exec(select(CrmLeadContactSnapshot).where(CrmLeadContactSnapshot.lead_id == lead.id)).first()
        item["lead_item"] = lead_item.model_dump() if lead_item else None
        item["contact_snapshot"] = snapshot.model_dump() if snapshot else None
        result.append(item)
    return result


@app.put("/api/v1/leads/{lead_id}")
@app.put("/api/admin/leads/{lead_id}")
def update_lead(lead_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, bool]:
    lead = session.get(CrmLead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    allowed = {
        "current_stage_id",
        "assigned_department_id",
        "assigned_manager_user_id",
        "status_code",
        "priority_code",
        "hot_flag",
        "summary",
        "next_action",
        "manager_comment",
        "counterparty_id",
    }
    for key, value in payload.items():
        if key in allowed:
            setattr(lead, key, value)
    lead.updated_at = _now()
    if lead.status_code in {"closed", "blocked"}:
        lead.closed_at = _now()
    session.add(lead)
    session.commit()
    return {"ok": True}


def _crud_list(session: Session, model):
    return session.exec(select(model)).all()


def _crud_create(session: Session, model, payload: dict[str, Any]):
    row = model(**payload)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _crud_update(session: Session, model, row_id: int, payload: dict[str, Any]):
    row = session.get(model, row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    for key, value in payload.items():
        if hasattr(row, key):
            setattr(row, key, value)
    if hasattr(row, "updated_at"):
        setattr(row, "updated_at", _now())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _crud_delete(session: Session, model, row_id: int) -> dict[str, bool]:
    row = session.get(model, row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    session.delete(row)
    session.commit()
    return {"ok": True}


@app.get("/api/v1/catalog/commodities")
@app.get("/api/admin/products")
def get_commodities(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    rows = _crud_list(session, RefCommodity)
    return sorted(rows, key=lambda x: (x.sort_order, x.name))


@app.post("/api/v1/catalog/commodities")
def create_commodity(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_create(session, RefCommodity, payload)


@app.put("/api/v1/catalog/commodities/{row_id}")
def update_commodity(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, RefCommodity, row_id, payload)


@app.delete("/api/v1/catalog/commodities/{row_id}")
def delete_commodity(row_id: int, _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_delete(session, RefCommodity, row_id)


@app.get("/api/v1/catalog/regions")
def get_regions(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, RefRegion)


@app.put("/api/v1/catalog/regions/{row_id}")
def update_region(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, RefRegion, row_id, payload)


@app.post("/api/v1/catalog/regions")
def create_region(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_create(session, RefRegion, payload)


@app.get("/api/v1/catalog/transport-modes")
def get_transport_modes(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, RefTransportMode)


@app.post("/api/v1/catalog/transport-modes")
def create_transport_mode(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_create(session, RefTransportMode, payload)


@app.put("/api/v1/catalog/transport-modes/{row_id}")
def update_transport_mode(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, RefTransportMode, row_id, payload)


@app.get("/api/v1/catalog/delivery-basis")
def get_delivery_basis(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, RefDeliveryBasis)


@app.post("/api/v1/catalog/delivery-basis")
def create_delivery_basis(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_create(session, RefDeliveryBasis, payload)


@app.put("/api/v1/catalog/delivery-basis/{row_id}")
def update_delivery_basis(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, RefDeliveryBasis, row_id, payload)


@app.get("/api/v1/catalog/quality-templates")
def get_quality_templates(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    templates = _crud_list(session, CatalogQualityTemplate)
    result = []
    for item in templates:
        payload = item.model_dump()
        payload["lines"] = [
            line.model_dump()
            for line in session.exec(
                select(CatalogQualityTemplateLine).where(CatalogQualityTemplateLine.quality_template_id == item.id)
            ).all()
        ]
        result.append(payload)
    return result


@app.post("/api/v1/catalog/quality-templates")
def create_quality_template(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    lines = payload.pop("lines", [])
    row = _crud_create(session, CatalogQualityTemplate, payload)
    for line in lines:
        line["quality_template_id"] = row.id
        _crud_create(session, CatalogQualityTemplateLine, line)
    return row


@app.put("/api/v1/catalog/quality-templates/{row_id}")
def update_quality_template(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    lines = payload.pop("lines", None)
    row = _crud_update(session, CatalogQualityTemplate, row_id, payload)
    if lines is not None:
        for existing in session.exec(
            select(CatalogQualityTemplateLine).where(CatalogQualityTemplateLine.quality_template_id == row_id)
        ).all():
            session.delete(existing)
        session.commit()
        for line in lines:
            line["quality_template_id"] = row_id
            _crud_create(session, CatalogQualityTemplateLine, line)
    return row


@app.get("/api/v1/catalog/price-policies")
def get_price_policies(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, CatalogPricePolicy)


@app.post("/api/v1/catalog/price-policies")
def create_price_policy(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_create(session, CatalogPricePolicy, payload)


@app.put("/api/v1/catalog/price-policies/{row_id}")
def update_price_policy(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, CatalogPricePolicy, row_id, payload)


@app.get("/api/v1/catalog/lots")
def get_lots(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, CatalogStockPlaceholder)


@app.post("/api/v1/catalog/lots")
def create_lot(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_create(session, CatalogStockPlaceholder, payload)


@app.put("/api/v1/catalog/lots/{row_id}")
def update_lot(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, CatalogStockPlaceholder, row_id, payload)


@app.get("/api/v1/knowledge")
def get_knowledge(session: Session = Depends(get_session), group: Optional[str] = Query(default=None)):
    rows = session.exec(select(KnowledgeArticle).where(KnowledgeArticle.is_active == True)).all()
    if group:
        rows = [item for item in rows if item.article_group == group]
    return sorted(rows, key=lambda x: (x.sort_order, x.title))


@app.get("/api/v1/admin/knowledge")
def get_knowledge_admin(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, KnowledgeArticle)


@app.post("/api/v1/admin/knowledge")
def create_knowledge(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_create(session, KnowledgeArticle, payload)


@app.put("/api/v1/admin/knowledge/{row_id}")
def update_knowledge(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, KnowledgeArticle, row_id, payload)


@app.get("/api/v1/admin/chat-sessions")
def get_chat_sessions(_: None = Depends(require_admin), session: Session = Depends(get_session), limit: int = 200):
    rows = session.exec(select(ChatSession)).all()
    return sorted(rows, key=lambda x: x.updated_at, reverse=True)[:limit]


@app.get("/api/v1/admin/chat-sessions/{session_id}")
def get_chat_session_detail(session_id: int, _: None = Depends(require_admin), session: Session = Depends(get_session)):
    chat = session.get(ChatSession, session_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = sorted(session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)).all(), key=lambda x: x.created_at)
    facts = session.exec(select(ChatExtractedFact).where(ChatExtractedFact.session_id == session_id)).all()
    missing = session.exec(select(ChatMissingField).where(ChatMissingField.session_id == session_id)).all()
    checkpoints = sorted(
        session.exec(select(ChatQualificationCheckpoint).where(ChatQualificationCheckpoint.session_id == session_id)).all(),
        key=lambda x: x.created_at,
    )
    lead = session.get(CrmLead, chat.lead_id) if chat.lead_id else None
    return {
        "session": chat,
        "lead": lead,
        "messages": messages,
        "facts": facts,
        "missing_fields": missing,
        "checkpoints": checkpoints,
    }


@app.get("/api/v1/admin/counterparties")
def get_counterparties(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, CrmCounterparty)


@app.post("/api/v1/admin/counterparties")
def create_counterparty(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_create(session, CrmCounterparty, payload)


@app.put("/api/v1/admin/counterparties/{row_id}")
def update_counterparty(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, CrmCounterparty, row_id, payload)


@app.get("/api/v1/admin/tasks")
def get_tasks(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    rows = _crud_list(session, CrmTask)
    return sorted(rows, key=lambda x: x.created_at, reverse=True)


@app.post("/api/v1/admin/tasks")
def create_task(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_create(session, CrmTask, payload)


@app.put("/api/v1/admin/tasks/{row_id}")
def update_task(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, CrmTask, row_id, payload)


@app.get("/api/v1/admin/users")
def get_users(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, AdminUser)


@app.post("/api/v1/admin/users")
def create_user(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    if "password_hash" not in payload and "password" in payload:
        payload["password_hash"] = _hash_password(str(payload.pop("password")))
    return _crud_create(session, AdminUser, payload)


@app.put("/api/v1/admin/users/{row_id}")
def update_user(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    if "password" in payload:
        payload["password_hash"] = _hash_password(str(payload.pop("password")))
    return _crud_update(session, AdminUser, row_id, payload)


@app.get("/api/v1/admin/settings")
def get_settings(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    rows = list(_crud_list(session, AdminSetting))
    return _mask_settings(rows)


@app.put("/api/v1/admin/settings/{row_id}")
def update_setting(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    row = session.get(AdminSetting, row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    if row.is_secret and payload.get("setting_value") == "***":
        payload.pop("setting_value", None)
    updated = _crud_update(session, AdminSetting, row_id, payload)
    result = updated.model_dump()
    if updated.is_secret:
        result["setting_value"] = "***"
    return result


@app.get("/api/v1/admin/reference/request-types")
def get_request_types(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, RefRequestType)


@app.get("/api/v1/admin/reference/lead-sources")
def get_lead_sources(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, RefLeadSource)


@app.get("/api/v1/admin/reference/departments")
def get_departments(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, RefDepartment)


@app.get("/api/v1/admin/reference/manager-roles")
def get_manager_roles(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, RefManagerRole)


@app.get("/api/v1/admin/reference/counterparty-types")
def get_counterparty_types(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_list(session, RefCounterpartyType)
