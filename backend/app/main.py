import asyncio
import json
import logging
import os
from datetime import timedelta
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .agent import SalesAssistantAgent
from .db import engine, get_session, init_db
from .guardrails import evaluate_guardrails
from .guardrail_response_policy import render_guardrail_reply
from .llm_service import LLMService, LLMUnavailableError
from .models import (
    AdminSession,
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
    RefQualityParameter,
    RefRegion,
    RefRequestType,
    RefTransportMode,
)
from .negotiation import build_offer_hypothesis, resolve_negotiation_stage
from .rag_service import render_rag_lines, retrieve_knowledge_context
from .sales_logic import (
    detect_request_type,
    extract_facts,
    human_field_name,
    next_missing_field,
    next_question_for,
    required_fields,
)
from .security import generate_session_token, hash_password, hash_session_token, verify_password
from .seed import seed_defaults
from .services.conversation_service import ConversationService
from .services.telegram_service import TelegramService

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("agrolead.api")
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="AgroLead Assistant API", version="7.0.0")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
ALLOW_STATIC_ADMIN_TOKEN = os.getenv("ALLOW_STATIC_ADMIN_TOKEN", "0").strip().lower() not in {"0", "false", "no", "off"}
ADMIN_SESSION_TTL_MINUTES = max(10, min(int(os.getenv("ADMIN_SESSION_TTL_MINUTES", "720")), 60 * 24 * 30))

llm_service = LLMService()
agent = SalesAssistantAgent(llm_service=llm_service)
conversation_service = ConversationService(llm_service=llm_service)
telegram_service = TelegramService(conversation_service=conversation_service)

COMMODITY_NAME_SYNONYMS = {
    "пшениц": "пшеница",
    "фураж": "пшеница",
    "ячмен": "ячмень",
    "кукуруз": "кукуруза",
    "подсолнеч": "подсолнечник",
    "семечк": "подсолнечник",
}

REGION_NAME_SYNONYMS = {
    "крд": "краснодар",
    "краснодарский край": "краснодар",
    "ростов": "ростов-на-дону",
    "ростовская область": "ростов-на-дону",
    "новорос": "новороссийск",
}


class LoginIn(BaseModel):
    username: str
    password: str


class ChatIn(BaseModel):
    text: str
    session_id: Optional[str | int] = None
    client_id: str = "web"
    source_channel: str = "web_widget"
    external_user_id: Optional[str] = None
    external_chat_id: Optional[str] = None
    debug: bool = False


class ChatDryRunIn(BaseModel):
    text: str
    debug: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _create_admin_session(
    session: Session,
    user_id: Optional[int],
    user_agent: str = "",
    remote_addr: str = "",
) -> str:
    token = generate_session_token()
    row = AdminSession(
        user_id=user_id,
        token_hash=hash_session_token(token),
        expires_at=_now() + timedelta(minutes=ADMIN_SESSION_TTL_MINUTES),
        user_agent=(user_agent or "")[:255],
        remote_addr=(remote_addr or "")[:128],
    )
    session.add(row)
    session.commit()
    return token


def require_admin(
    x_admin_token: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> Optional[AdminUser]:
    token = (x_admin_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if ALLOW_STATIC_ADMIN_TOKEN and ADMIN_TOKEN and token == ADMIN_TOKEN:
        return None

    now = _now()
    token_hash = hash_session_token(token)
    admin_session = session.exec(select(AdminSession).where(AdminSession.token_hash == token_hash)).first()
    if not admin_session:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if admin_session.revoked_at is not None or admin_session.expires_at <= now:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = session.get(AdminUser, admin_session.user_id) if admin_session.user_id else None
    if admin_session.user_id and (not user or not user.is_active):
        admin_session.revoked_at = now
        session.add(admin_session)
        session.commit()
        raise HTTPException(status_code=401, detail="Unauthorized")

    admin_session.last_seen_at = now
    session.add(admin_session)
    session.commit()
    return user




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
        commodity_map[row.code.lower()] = commodity_id
        if row.full_name:
            commodity_map[row.full_name.lower()] = commodity_id

    for alias, canonical in COMMODITY_NAME_SYNONYMS.items():
        if canonical in commodity_map:
            commodity_map.setdefault(alias, commodity_map[canonical])

    regions = session.exec(select(RefRegion).where(RefRegion.is_active == True)).all()
    region_map: dict[str, int] = {}
    for row in regions:
        region_id = _require_id(row.id, "region")
        region_map[row.code.lower()] = region_id
        if row.region_name:
            region_map[row.region_name.lower()] = region_id
        if row.city_name:
            region_map[row.city_name.lower()] = region_id
        if row.port_name:
            region_map[row.port_name.lower()] = region_id

    for alias, canonical in REGION_NAME_SYNONYMS.items():
        if canonical in region_map:
            region_map.setdefault(alias, region_map[canonical])
    return commodity_map, region_map




async def _process_chat(session: Session, payload: ChatIn) -> dict[str, Any]:
    result = await conversation_service.handle_message(
        text=payload.text,
        session_id=payload.session_id,
        client_id=payload.client_id,
        source_channel=payload.source_channel,
        external_user_id=payload.external_user_id,
        metadata={"external_chat_id": payload.external_chat_id} if payload.external_chat_id else None,
        db_session=session,
    )
    out = result.to_dict()
    out["request_type_v7"] = result.known_facts.get("request_type") if result.known_facts else None
    return out


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
        "agent_engine": "sales-lead-orchestrator-v7",
        "compat_engine": "sales-lead-orchestrator-v6",
        "db_ok": db_ok,
        "telegram": {
            "enabled": telegram_service.enabled,
            "webhook_url": telegram_service.webhook_url if telegram_service.enabled else "",
        },
    }


@app.get("/api/llm/status")
def llm_status(_: None = Depends(require_admin)) -> dict[str, Any]:
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
                "request_type_v7": result.get("request_type_v7"),
                "status": result.get("status"),
                "stage": result.get("stage"),
                "state": result.get("state"),
                "provider": result.get("provider"),
                "model": result.get("model"),
                "captured_fields": result.get("captured_fields") or [],
                "known_facts": result.get("known_facts") or {},
                "uncertain_facts": result.get("uncertain_facts") or {},
                "missing_fields": result.get("missing_fields") or [],
                "qualification_score": result.get("qualification_score", 0),
                "source_channel": result.get("source_channel") or payload.source_channel,
                "next_action": result.get("next_action") or "",
                "negotiation_stage": result.get("negotiation_stage") or "qualification",
                "token": text,
                "text": text,
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
        return {
            "done": True,
            "provider": "guardrails",
            "model": "policy-v2",
            "text": render_guardrail_reply(guard, user_text=text, last_assistant_messages=[]),
            "guardrail": {
                "decision_code": guard.decision_code,
                "severity": guard.severity,
                "policy_tags": list(guard.policy_tags),
            },
        }

    try:
        request_code = detect_request_type(text)
        request = _get_ref_by_code(session, RefRequestType, request_code)
        commodity_map, region_map = _resolve_maps(session)
        facts = extract_facts(text, commodity_map, region_map)
        _resolve_code_facts(session, facts)
        required = required_fields(request_code)
        collected = {key for key, value in facts.items() if value.confidence >= 0.68 and (value.text or value.numeric is not None)}
        missing = next_missing_field(required, collected)
        question = next_question_for(missing) if missing else "Если готовы, фиксирую заявку и передаю менеджеру."
        summary = [f"{human_field_name(k)}: {v.text}" for k, v in facts.items() if v.text][:8]

        commodity_id: Optional[int] = None
        commodity_fact = facts.get("commodity_id")
        if commodity_fact and commodity_fact.numeric is not None:
            commodity_id = int(commodity_fact.numeric)

        has_price_policy = bool(
            session.exec(select(CatalogPricePolicy).where(CatalogPricePolicy.is_active == True)).all()
        )
        has_stock_hint = bool(
            commodity_id
            and session.exec(
                select(CatalogStockPlaceholder).where(
                    CatalogStockPlaceholder.is_active == True,
                    CatalogStockPlaceholder.commodity_id == commodity_id,
                )
            ).all()
        )

        rag_chunks = retrieve_knowledge_context(
            session,
            query_text=text,
            request_type_id=request.id if request and request.id else None,
            commodity_id=commodity_id,
            article_group="faq" if _is_faq_like(text) else None,
            top_k=4,
        )
        offer_lines = build_offer_hypothesis(
            request_code,
            {k: v.text for k, v in facts.items() if v.text},
            has_price_policy=has_price_policy,
            has_stock_hint=has_stock_hint,
            missing_field=missing,
        )
        stage = "faq" if _is_faq_like(text) else "draft"
        negotiation_stage = resolve_negotiation_stage(stage, "draft", text, missing)
        reply = await agent.reply(
            stage=stage,
            request_type_name=request.name if request else request_code,
            user_text=text,
            summary_lines=summary,
            next_question=question,
            last_assistant_messages=[],
            rag_lines=render_rag_lines(rag_chunks),
            offer_lines=offer_lines,
            negotiation_stage=negotiation_stage,
        )
        if reply.provider == "service-unavailable":
            detail = llm_service.last_error or "LLM unavailable"
            raise HTTPException(status_code=503, detail=detail)
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    result: dict[str, Any] = {"done": True, "provider": reply.provider, "model": reply.model, "text": reply.text}
    if payload.debug:
        result["debug"] = {
            "request_type": request_code,
            "rag_article_ids": [item.article_id for item in rag_chunks],
            "offer_lines": offer_lines,
            "negotiation_stage": negotiation_stage,
        }
    return result


@app.get("/api/public/bootstrap")
def bootstrap(session: Session = Depends(get_session)) -> dict[str, Any]:
    company = session.exec(select(CompanyProfile)).first()
    commodities = session.exec(select(RefCommodity).where(RefCommodity.is_active == True)).all()
    request_types = session.exec(select(RefRequestType).where(RefRequestType.is_active == True)).all()
    settings_rows = session.exec(select(AdminSetting).where(AdminSetting.is_secret == False)).all()
    public_settings: dict[str, str] = {}
    for item in settings_rows:
        if item.setting_key.startswith("ui.") or item.setting_key.startswith("intake.") or item.setting_key.startswith("routing."):
            public_settings[item.setting_key] = item.setting_value
    llm = llm_service.status()
    public_llm = {
        "mode": llm.get("mode"),
        "preferred_provider": llm.get("preferred_provider"),
        "gigachat_enabled": llm.get("gigachat_enabled"),
        "models": llm.get("models"),
    }
    return {
        "company": company,
        "commodities": commodities,
        "request_types": request_types,
        "settings": public_settings,
        "llm": public_llm,
        "telegram": {
            "enabled": telegram_service.enabled,
            "webhook_url": telegram_service.webhook_url if telegram_service.enabled else "",
        },
    }


@app.post("/api/integrations/telegram/webhook")
async def telegram_webhook(
    payload: dict[str, Any],
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if secret and x_telegram_bot_api_secret_token != secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    return await telegram_service.handle_update(payload, db_session=session)


@app.get("/api/admin/telegram/status")
@app.get("/api/v1/admin/telegram/status")
async def telegram_status(_: None = Depends(require_admin)) -> dict[str, Any]:
    info = await telegram_service.get_webhook_info()
    return {
        "enabled": telegram_service.enabled,
        "token_configured": telegram_service.enabled,
        "webhook_url": telegram_service.webhook_url if telegram_service.enabled else "",
        "last_error": telegram_service.last_error,
        "raw": info,
    }


@app.post("/api/admin/telegram/set-webhook")
@app.post("/api/v1/admin/telegram/set-webhook")
async def telegram_set_webhook(
    payload: Optional[dict[str, Any]] = None,
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    url = (payload or {}).get("url") if isinstance(payload, dict) else None
    return await telegram_service.set_webhook(url=url)


@app.post("/api/admin/login")
@app.post("/api/v1/admin/login")
def admin_login(
    payload: LoginIn,
    session: Session = Depends(get_session),
    user_agent: Optional[str] = Header(default="", alias="User-Agent"),
    x_forwarded_for: Optional[str] = Header(default="", alias="X-Forwarded-For"),
) -> dict[str, str]:
    user = session.exec(select(AdminUser).where(AdminUser.login == payload.username, AdminUser.is_active == True)).first()
    fallback_ok = payload.username == ADMIN_USER and payload.password == ADMIN_PASS

    if user:
        valid, needs_rehash = verify_password(payload.password, user.password_hash)
        if not valid:
            raise HTTPException(status_code=401, detail="Bad credentials")
        if needs_rehash:
            user.password_hash = hash_password(payload.password)
            user.updated_at = _now()
            session.add(user)
            session.commit()

        token = _create_admin_session(
            session,
            user_id=user.id,
            user_agent=user_agent or "",
            remote_addr=(x_forwarded_for or "").split(",")[0].strip(),
        )
        return {"token": token}

    if fallback_ok and ALLOW_STATIC_ADMIN_TOKEN and ADMIN_TOKEN:
        return {"token": ADMIN_TOKEN}

    raise HTTPException(status_code=401, detail="Bad credentials")


@app.post("/api/v1/admin/logout")
def admin_logout(
    x_admin_token: Optional[str] = Header(default=None),
    _: Optional[AdminUser] = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, bool]:
    token = (x_admin_token or "").strip()
    if not token:
        return {"ok": True}

    row = session.exec(select(AdminSession).where(AdminSession.token_hash == hash_session_token(token))).first()
    if row and row.revoked_at is None:
        row.revoked_at = _now()
        row.last_seen_at = _now()
        session.add(row)
        session.commit()
    return {"ok": True}


@app.get("/api/v1/admin/stats")
@app.get("/api/admin/stats")
def admin_stats(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, Any]:
    leads = session.exec(select(CrmLead)).all()
    sessions = session.exec(select(ChatSession)).all()
    tasks = session.exec(select(CrmTask)).all()
    request_types = {row.id: row.code for row in session.exec(select(RefRequestType)).all()}
    sources = {row.id: row.code for row in session.exec(select(RefLeadSource)).all()}

    by_request: dict[str, int] = {}
    by_source: dict[str, int] = {}
    scores: list[int] = []
    v7_required = {"request_type", "product", "volume", "region", "timing", "contact"}
    for lead in leads:
        code = request_types.get(lead.request_type_id, "unknown")
        by_request[code] = by_request.get(code, 0) + 1
        source_code = sources.get(lead.source_id, "unknown")
        by_source[source_code] = by_source.get(source_code, 0) + 1
        fields = session.exec(select(ChatMissingField).where(ChatMissingField.lead_id == lead.id)).all()
        v7_fields = [field for field in fields if field.field_code in v7_required]
        if v7_fields:
            collected = len([field for field in v7_fields if field.is_collected])
            scores.append(int(round(collected / len(v7_required) * 100)))
        elif lead.status_code == "qualified":
            scores.append(100)
        elif lead.status_code == "partially_qualified":
            scores.append(67)
        else:
            scores.append(0)

    stage_counts: dict[str, int] = {}
    for lead in leads:
        stage_counts[lead.status_code] = stage_counts.get(lead.status_code, 0) + 1

    return {
        "leads_total": len(leads),
        "hot_leads": len([lead for lead in leads if lead.hot_flag]),
        "unassigned_leads": len([lead for lead in leads if not lead.assigned_manager_user_id]),
        "sessions_total": len(sessions),
        "tasks_open": len([task for task in tasks if task.status != "done"]),
        "ready_for_manager": len([lead for lead in leads if lead.status_code in {"qualified", "handed_to_manager"}]),
        "avg_qualification_score": int(round(sum(scores) / len(scores))) if scores else 0,
        "by_request_type": by_request,
        "by_stage": stage_counts,
        "by_source_channel": by_source,
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
    limit = max(1, min(limit, 1000))
    leads = session.exec(select(CrmLead)).all()
    request_type_by_id = {row.id: row.code for row in session.exec(select(RefRequestType)).all()}
    source_by_id = {row.id: row.code for row in session.exec(select(RefLeadSource)).all()}
    v7_required = {"request_type", "product", "volume", "region", "timing", "contact"}

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
        item["source_channel"] = source_by_id.get(lead.source_id, "unknown")
        lead_item = session.exec(select(CrmLeadItem).where(CrmLeadItem.lead_id == lead.id)).first()
        snapshot = session.exec(select(CrmLeadContactSnapshot).where(CrmLeadContactSnapshot.lead_id == lead.id)).first()
        missing_rows = session.exec(select(ChatMissingField).where(ChatMissingField.lead_id == lead.id)).all()
        v7_rows = [row for row in missing_rows if row.field_code in v7_required]
        if v7_rows:
            collected = len([row for row in v7_rows if row.is_collected])
            item["qualification_score"] = int(round(collected / len(v7_required) * 100))
        elif lead.status_code == "qualified":
            item["qualification_score"] = 100
        elif lead.status_code == "partially_qualified":
            item["qualification_score"] = 67
        else:
            item["qualification_score"] = 0
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


def _crud_upsert_by_code(session: Session, model, payload: dict[str, Any]):
    code = str(payload.get("code", "")).strip()
    if not code:
        return _crud_create(session, model, payload)

    existing = _get_ref_by_code(session, model, code)
    if not existing:
        return _crud_create(session, model, payload)

    for key, value in payload.items():
        if key == "id":
            continue
        if hasattr(existing, key):
            setattr(existing, key, value)
    if hasattr(existing, "updated_at"):
        setattr(existing, "updated_at", _now())
    session.add(existing)
    session.commit()
    session.refresh(existing)
    return existing


def _to_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_quality_line_payload(session: Session, raw_line: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw_line or {})
    if "operator" in payload and "comparison_operator" not in payload:
        payload["comparison_operator"] = payload.pop("operator")

    if "parameter_code" in payload and "quality_parameter_id" not in payload:
        row = _get_ref_by_code(session, RefQualityParameter, str(payload.pop("parameter_code")).strip().lower())
        if row and row.id:
            payload["quality_parameter_id"] = row.id

    if "target_value" in payload:
        parsed = _to_float_or_none(payload["target_value"])
        if parsed is not None:
            payload["target_value_numeric"] = parsed
            payload.setdefault("target_value_text", str(payload["target_value"]))
        else:
            payload["target_value_text"] = str(payload["target_value"])
        payload.pop("target_value", None)

    allowed = {
        "quality_parameter_id",
        "comparison_operator",
        "target_value_numeric",
        "target_value_text",
        "sort_order",
    }
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if key in allowed:
            normalized[key] = value
    return normalized


def _normalize_quality_template_payload(session: Session, raw_payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = dict(raw_payload or {})
    if "code" in payload and "template_code" not in payload:
        payload["template_code"] = payload.pop("code")
    if "name" in payload and "template_name" not in payload:
        payload["template_name"] = payload.pop("name")

    payload.pop("description", None)
    lines_raw = payload.pop("lines", []) or []
    lines = [_normalize_quality_line_payload(session, item) for item in lines_raw if isinstance(item, dict)]

    allowed = {"commodity_id", "template_code", "template_name", "is_default", "is_active"}
    normalized_payload: dict[str, Any] = {}
    for key, value in payload.items():
        if key in allowed:
            normalized_payload[key] = value
    return normalized_payload, lines


def _normalize_price_policy_payload(session: Session, raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw_payload or {})
    if "price_formula_text" in payload and "pricing_rule_text" not in payload:
        payload["pricing_rule_text"] = payload.pop("price_formula_text")

    if "region_id" in payload and "source_region_id" not in payload:
        payload["source_region_id"] = payload.get("region_id")
    payload.pop("region_id", None)

    currency_code = str(payload.pop("currency_code", "")).strip()
    if currency_code:
        current_rule = str(payload.get("pricing_rule_text", "")).strip()
        if current_rule:
            payload["pricing_rule_text"] = f"[{currency_code}] {current_rule}"

    request_type_code = str(payload.pop("request_type_code", "")).strip().lower()
    if request_type_code and "request_type_id" not in payload:
        row = _get_ref_by_code(session, RefRequestType, request_type_code)
        if row and row.id:
            payload["request_type_id"] = row.id

    transport_mode_code = str(payload.pop("transport_mode_code", "")).strip().lower()
    if transport_mode_code and "transport_mode_id" not in payload:
        row = _get_ref_by_code(session, RefTransportMode, transport_mode_code)
        if row and row.id:
            payload["transport_mode_id"] = row.id

    allowed = {
        "code",
        "name",
        "commodity_id",
        "request_type_id",
        "source_region_id",
        "destination_region_id",
        "transport_mode_id",
        "min_volume",
        "max_volume",
        "pricing_rule_text",
        "manager_note",
        "is_active",
        "valid_from",
        "valid_to",
    }
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if key in allowed:
            normalized[key] = value
    return normalized


@app.get("/api/v1/catalog/commodities")
@app.get("/api/admin/products")
def get_commodities(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    rows = _crud_list(session, RefCommodity)
    return sorted(rows, key=lambda x: (x.sort_order, x.name))


@app.post("/api/v1/catalog/commodities")
def create_commodity(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_upsert_by_code(session, RefCommodity, payload)


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
    parameter_by_id = {row.id: row.code for row in session.exec(select(RefQualityParameter)).all()}
    result = []
    for item in templates:
        payload = item.model_dump()
        payload["code"] = payload.get("template_code", "")
        payload["name"] = payload.get("template_name", "")
        lines = []
        for line in session.exec(
            select(CatalogQualityTemplateLine).where(CatalogQualityTemplateLine.quality_template_id == item.id)
        ).all():
            line_payload = line.model_dump()
            line_payload["parameter_code"] = parameter_by_id.get(line.quality_parameter_id, "")
            line_payload["operator"] = line.comparison_operator
            line_payload["target_value"] = (
                line.target_value_numeric if line.target_value_numeric is not None else line.target_value_text
            )
            lines.append(line_payload)
        payload["lines"] = lines
        result.append(payload)
    return result


@app.post("/api/v1/catalog/quality-templates")
def create_quality_template(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    normalized, lines = _normalize_quality_template_payload(session, payload)
    if "template_code" not in normalized or "template_name" not in normalized:
        raise HTTPException(status_code=422, detail="template_code and template_name are required")

    existing = session.exec(
        select(CatalogQualityTemplate).where(CatalogQualityTemplate.template_code == normalized["template_code"])
    ).first()
    if existing:
        for key, value in normalized.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        existing.updated_at = _now()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        row = existing
        row_id = _require_id(row.id, "quality template")
        for line_row in session.exec(
            select(CatalogQualityTemplateLine).where(CatalogQualityTemplateLine.quality_template_id == row_id)
        ).all():
            session.delete(line_row)
        session.commit()
    else:
        row = _crud_create(session, CatalogQualityTemplate, normalized)

    row_id = _require_id(row.id, "quality template")
    for line in lines:
        if "quality_parameter_id" not in line:
            continue
        line["quality_template_id"] = row_id
        _crud_create(session, CatalogQualityTemplateLine, line)
    row_payload = row.model_dump()
    row_payload["template_code"] = row_payload.get("template_code") or normalized.get("template_code")
    row_payload["template_name"] = row_payload.get("template_name") or normalized.get("template_name")
    row_payload["code"] = row_payload.get("template_code", "")
    row_payload["name"] = row_payload.get("template_name", "")
    return {
        **row_payload,
        "lines": [
            item.model_dump()
            for item in session.exec(
                select(CatalogQualityTemplateLine).where(CatalogQualityTemplateLine.quality_template_id == row_id)
            ).all()
        ],
    }


@app.put("/api/v1/catalog/quality-templates/{row_id}")
def update_quality_template(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    lines_provided = "lines" in payload
    normalized, lines = _normalize_quality_template_payload(session, payload)
    row = _crud_update(session, CatalogQualityTemplate, row_id, normalized)
    if lines_provided:
        for existing in session.exec(
            select(CatalogQualityTemplateLine).where(CatalogQualityTemplateLine.quality_template_id == row_id)
        ).all():
            session.delete(existing)
        session.commit()
        for line in lines:
            if "quality_parameter_id" not in line:
                continue
            line["quality_template_id"] = row_id
            _crud_create(session, CatalogQualityTemplateLine, line)
    row_payload = row.model_dump()
    row_payload["code"] = row_payload.get("template_code", "")
    row_payload["name"] = row_payload.get("template_name", "")
    return {
        **row_payload,
        "lines": [
            item.model_dump()
            for item in session.exec(
                select(CatalogQualityTemplateLine).where(CatalogQualityTemplateLine.quality_template_id == row_id)
            ).all()
        ],
    }


@app.get("/api/v1/catalog/price-policies")
def get_price_policies(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    rows = _crud_list(session, CatalogPricePolicy)
    result = []
    for row in rows:
        payload = row.model_dump()
        payload["region_id"] = payload.get("source_region_id")
        payload["price_formula_text"] = payload.get("pricing_rule_text")
        result.append(payload)
    return result


@app.post("/api/v1/catalog/price-policies")
def create_price_policy(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    normalized = _normalize_price_policy_payload(session, payload)
    if not normalized.get("pricing_rule_text"):
        normalized["pricing_rule_text"] = "условия расчета уточняются"
    return _crud_upsert_by_code(session, CatalogPricePolicy, normalized)


@app.put("/api/v1/catalog/price-policies/{row_id}")
def update_price_policy(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    normalized = _normalize_price_policy_payload(session, payload)
    return _crud_update(session, CatalogPricePolicy, row_id, normalized)


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
    return _crud_upsert_by_code(session, KnowledgeArticle, payload)


@app.put("/api/v1/admin/knowledge/{row_id}")
def update_knowledge(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    return _crud_update(session, KnowledgeArticle, row_id, payload)


@app.get("/api/v1/admin/chat-sessions")
def get_chat_sessions(_: None = Depends(require_admin), session: Session = Depends(get_session), limit: int = 200):
    limit = max(1, min(limit, 1000))
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
    rows = _crud_list(session, AdminUser)
    result = []
    for row in rows:
        payload = row.model_dump()
        payload.pop("password_hash", None)
        result.append(payload)
    return result


@app.post("/api/v1/admin/users")
def create_user(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    if "password_hash" not in payload and "password" in payload:
        payload["password_hash"] = hash_password(str(payload.pop("password")))
    return _crud_create(session, AdminUser, payload)


@app.put("/api/v1/admin/users/{row_id}")
def update_user(row_id: int, payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    if "password" in payload:
        payload["password_hash"] = hash_password(str(payload.pop("password")))
    return _crud_update(session, AdminUser, row_id, payload)


@app.get("/api/v1/admin/settings")
def get_settings(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    rows = list(_crud_list(session, AdminSetting))
    return _mask_settings(rows)


@app.post("/api/v1/admin/settings")
def create_setting(payload: dict[str, Any], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    existing = session.exec(select(AdminSetting).where(AdminSetting.setting_key == payload.get("setting_key", ""))).first()
    if existing:
        raise HTTPException(status_code=409, detail="Setting key already exists")
    row = _crud_create(session, AdminSetting, payload)
    result = row.model_dump()
    if row.is_secret:
        result["setting_value"] = "***"
    return result


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


@app.get("/api/v1/admin/leads/{lead_id}/workspace")
def get_lead_workspace(lead_id: int, _: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, Any]:
    lead = session.get(CrmLead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead_item = session.exec(select(CrmLeadItem).where(CrmLeadItem.lead_id == lead_id)).first()
    contact = session.exec(select(CrmLeadContactSnapshot).where(CrmLeadContactSnapshot.lead_id == lead_id)).first()
    counterparty = session.get(CrmCounterparty, lead.counterparty_id) if lead.counterparty_id else None

    sessions = session.exec(select(ChatSession).where(ChatSession.lead_id == lead_id)).all()
    sessions = sorted(sessions, key=lambda x: x.updated_at, reverse=True)

    session_payloads = []
    latest = sessions[0] if sessions else None
    latest_messages = []
    latest_facts = []
    latest_missing = []
    latest_checkpoints = []

    for row in sessions:
        session_payloads.append(row.model_dump())

    if latest and latest.id:
        sid = latest.id
        latest_messages = sorted(
            session.exec(select(ChatMessage).where(ChatMessage.session_id == sid)).all(),
            key=lambda x: x.created_at,
        )
        latest_facts = session.exec(select(ChatExtractedFact).where(ChatExtractedFact.session_id == sid)).all()
        latest_missing = session.exec(select(ChatMissingField).where(ChatMissingField.session_id == sid)).all()
        latest_checkpoints = sorted(
            session.exec(select(ChatQualificationCheckpoint).where(ChatQualificationCheckpoint.session_id == sid)).all(),
            key=lambda x: x.created_at,
        )

    return {
        "lead": lead,
        "lead_item": lead_item,
        "contact_snapshot": contact,
        "counterparty": counterparty,
        "sessions": session_payloads,
        "latest_session_id": latest.id if latest else None,
        "messages": latest_messages,
        "facts": latest_facts,
        "missing_fields": latest_missing,
        "checkpoints": latest_checkpoints,
    }


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
