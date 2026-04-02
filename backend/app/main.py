import json
import logging
import os
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .agent import SingleAgentOrchestrator
from .db import engine, get_session, init_db
from .guardrails import evaluate_guardrails
from .llm_service import LLMService, LLMUnavailableError
from .models import (
    ChatMessage,
    ChatSession,
    CompanyProfile,
    ConversationState,
    Lead,
    ProductItem,
    PromptCategory,
    Scenario,
    ScenarioTemplate,
)
from .sales_logic import get_or_create_state, mark_toxic_stop
from .seed import reset_default_scenario_templates, seed_defaults

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("agrolead.api")

app = FastAPI(title="AgroLead API", version="4.0.0")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "315920")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "agrolead-admin-token")

llm_service = LLMService()
agent_orchestrator = SingleAgentOrchestrator(llm_service=llm_service)


class LoginIn(BaseModel):
    username: str
    password: str


class ChatIn(BaseModel):
    text: str
    session_id: Optional[int] = None
    client_id: str = "web"
    source_channel: str = "web"


class ChatDryRunIn(BaseModel):
    text: str


class PromptIn(BaseModel):
    key: str
    title: str
    content: str


class CompanyIn(BaseModel):
    name: str
    address: str
    phones: str
    email: str
    services: str
    contacts_markdown: str


class ScenarioIn(BaseModel):
    title: str
    description: str
    active: bool = True


class ProductIn(BaseModel):
    name: str
    culture: str
    grade: str = ""
    price_from: float = 0
    price_to: float = 0
    stock_tons: float = 0
    quality: str = ""
    location: str = ""
    active: bool = True


class LeadIn(BaseModel):
    session_id: Optional[int] = None
    client_name: str = ""
    phone: str = ""
    email: str = ""
    product: str = ""
    grade: str = ""
    volume_tons: str = ""
    region: str = ""
    delivery_term: str = ""
    status: str = "in_progress"
    source: str = "chat"
    source_channel: str = "web"
    raw_dialogue: str = ""
    comment: str = ""


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _now() -> datetime:
    return datetime.utcnow()


def sanitize_text(text: str) -> str:
    cleaned = (text or "").replace("Ассистент:", "").replace("Клиент:", "").strip()
    if len(cleaned) > 900:
        cleaned = cleaned[:900].rsplit(" ", 1)[0] + "..."
    return cleaned


def get_or_create_chat_session(session: Session, payload: ChatIn) -> ChatSession:
    chat_session = session.get(ChatSession, payload.session_id) if payload.session_id else None
    if chat_session:
        chat_session.updated_at = _now()
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)
        return chat_session

    chat_session = ChatSession(client_id=payload.client_id or str(uuid4()))
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session


def recent_history(session: Session, session_id: int, limit: int = 12) -> str:
    messages = list(session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)).all())
    messages.sort(key=lambda item: item.created_at)
    recent = messages[-limit:]
    rows = []
    for message in recent:
        role = "Клиент" if message.role == "user" else "Ассистент"
        rows.append(f"{role}: {message.text}")
    return "\n".join(rows)


def load_prompt_categories(session: Session) -> dict[str, str]:
    prompts = session.exec(select(PromptCategory)).all()
    return {prompt.key: prompt.content for prompt in prompts}


def sales_system_prompt(session: Session) -> str:
    company = session.exec(select(CompanyProfile)).first()
    products = list(session.exec(select(ProductItem).where(ProductItem.active == True)).all())
    products.sort(key=lambda item: item.stock_tons, reverse=True)
    products = products[:10]
    prompts = load_prompt_categories(session)

    identity = prompts.get(
        "identity",
        "Ты sales-ассистент ООО «Петрохлеб-Кубань». Твоя цель — быстро квалифицировать лид и передать его менеджеру.",
    )
    scope = prompts.get(
        "scope",
        "Работаешь только по зерновым сделкам: товар, класс, объем, регион, срок, контакт.",
    )
    safety = prompts.get(
        "safety",
        "Если пользователь токсичен — коротко отвечай и останавливай диалог. Если запрос про взлом — полный отказ.",
    )
    style = prompts.get(
        "style",
        "Стиль: живой, по-кубански, без канцелярщины. Не выдумывай факты и цены.",
    )

    company_block = ""
    if company:
        company_block = (
            f"Компания: {company.name}\n"
            f"Адрес: {company.address}\n"
            f"Телефоны: {company.phones}\n"
            f"Email: {company.email}\n"
            f"Услуги: {company.services}"
        )

    product_lines = [
        f"- {item.name}: {item.price_from:.0f}-{item.price_to:.0f} руб/т, остаток {item.stock_tons:.0f} т, {item.location}"
        for item in products
    ]
    catalog_block = "\n".join(product_lines)

    return (
        f"{identity}\n"
        f"{scope}\n"
        f"{safety}\n"
        f"{style}\n\n"
        f"{company_block}\n\n"
        f"Каталог:\n{catalog_block}\n\n"
        "Никогда не обещай то, чего нет в каталоге. Если данных не хватает, честно попроси уточнение."
    )


def save_assistant_message(
    session: Session,
    session_id: int,
    text: str,
    reason: str,
    provider: str,
    model: str,
    blocked: bool = False,
) -> None:
    session.add(
        ChatMessage(
            session_id=session_id,
            role="assistant",
            text=text,
            blocked=blocked,
            reason=reason,
            provider=provider,
            model_used=model,
        )
    )


async def process_chat(session: Session, payload: ChatIn) -> dict[str, Any]:
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    chat_session = get_or_create_chat_session(session, payload)
    session_id = chat_session.id
    if session_id is None:
        raise HTTPException(status_code=500, detail="session_id is not initialized")

    session.add(ChatMessage(session_id=session_id, role="user", text=text))
    session.commit()

    guard = evaluate_guardrails(text)
    state = get_or_create_state(session=session, session_id=session_id)

    if not guard.allowed:
        if guard.reason in {"security_block", "toxic_hard_stop"}:
            state = mark_toxic_stop(session=session, state=state, toxicity_level=guard.toxicity_level)
        else:
            state.toxicity_level = max(state.toxicity_level, guard.toxicity_level)
            state.updated_at = _now()
            session.add(state)
            session.commit()
            session.refresh(state)
        save_assistant_message(
            session=session,
            session_id=session_id,
            text=guard.answer,
            reason=guard.reason,
            provider="guardrails",
            model="rule-based",
            blocked=True,
        )
        session.commit()
        return {
            "session_id": session_id,
            "done": True,
            "text": guard.answer,
            "provider": "guardrails",
            "model": "rule-based",
            "state": state.state,
        }

    if state.state == "stopped_toxic":
        state.state = "greeting"
        state.updated_at = _now()
        session.add(state)
        session.commit()
        session.refresh(state)

    history = recent_history(session=session, session_id=session_id)
    result = await agent_orchestrator.handle(
        session=session,
        session_id=session_id,
        text=text,
        history=history,
        source_channel=payload.source_channel,
    )

    answer = sanitize_text(result.text)
    if not answer:
        answer = "Принял. Передаю менеджеру, он закрепит цену и логистику."

    save_assistant_message(
        session=session,
        session_id=session_id,
        text=answer,
        reason=result.reason,
        provider=result.provider,
        model=result.model,
    )
    session.commit()

    return {
        "session_id": session_id,
        "done": True,
        "text": answer,
        "provider": result.provider,
        "model": result.model,
        "state": result.state,
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
        session.exec(select(CompanyProfile)).first()
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "error",
        "time": datetime.utcnow().isoformat(),
        "agent_engine": "single-agent-orchestrator",
        "db_ok": db_ok,
    }


@app.get("/api/llm/status")
def llm_status() -> dict[str, Any]:
    return llm_service.status()


@app.post("/api/chat")
async def chat(payload: ChatIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    return await process_chat(session=session, payload=payload)


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatIn, session: Session = Depends(get_session)) -> StreamingResponse:
    result = await process_chat(session=session, payload=payload)

    def generator():
        yield json.dumps({"session_id": result["session_id"], "token": result["text"], "done": True}) + "\n"

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
            "model": "rule-based",
            "text": guard.answer,
            "reason": guard.reason,
        }

    try:
        answer, provider, model = await llm_service.complete(
            system_prompt=sales_system_prompt(session),
            user_prompt=f"Клиент: {text}",
            reason="dry-run",
        )
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "done": True,
        "provider": provider,
        "model": model,
        "text": sanitize_text(answer),
    }


@app.post("/api/admin/login")
def admin_login(payload: LoginIn) -> dict[str, str]:
    if payload.username != ADMIN_USER or payload.password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="Bad credentials")
    return {"token": ADMIN_TOKEN}


@app.get("/api/public/bootstrap")
def bootstrap(session: Session = Depends(get_session)) -> dict[str, Any]:
    products = list(session.exec(select(ProductItem).where(ProductItem.active == True)).all())
    products.sort(key=lambda item: item.stock_tons, reverse=True)
    return {
        "company": session.exec(select(CompanyProfile)).first(),
        "scenarios": session.exec(select(Scenario).where(Scenario.active == True)).all(),
        "products": products[:8],
        "llm": llm_service.status(),
    }


@app.get("/api/admin/stats")
def admin_stats(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, Any]:
    sessions = session.exec(select(ChatSession)).all()
    messages = session.exec(select(ChatMessage)).all()
    leads = session.exec(select(Lead)).all()
    states = session.exec(select(ConversationState)).all()

    return {
        "sessions": len(sessions),
        "messages": len(messages),
        "leads_total": len(leads),
        "leads_new": len([lead for lead in leads if lead.status == "in_progress"]),
        "leads_qualified": len([lead for lead in leads if lead.status == "qualified"]),
        "leads_blocked": len([lead for lead in leads if lead.status == "blocked"]),
        "state_machine": {
            "total": len(states),
            "greeting": len([state for state in states if state.state == "greeting"]),
            "qualification": len([state for state in states if state.state == "qualification"]),
            "handoff": len([state for state in states if state.state == "handoff"]),
            "stopped_toxic": len([state for state in states if state.state == "stopped_toxic"]),
        },
        "llm_usage": llm_service.status(),
    }


@app.get("/api/admin/chats")
def admin_chats(_: None = Depends(require_admin), session: Session = Depends(get_session), limit: int = 120) -> list[ChatMessage]:
    messages = list(session.exec(select(ChatMessage)).all())
    messages.sort(key=lambda item: item.created_at)
    return messages[-limit:]


@app.get("/api/admin/leads")
def get_leads(_: None = Depends(require_admin), session: Session = Depends(get_session), limit: int = 200) -> list[Lead]:
    leads = list(session.exec(select(Lead)).all())
    leads.sort(key=lambda item: item.updated_at, reverse=True)
    return leads[:limit]


@app.post("/api/admin/leads")
def post_lead(payload: LeadIn, _: None = Depends(require_admin), session: Session = Depends(get_session)) -> Lead:
    lead = Lead(**payload.model_dump(), created_at=_now(), updated_at=_now())
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


@app.put("/api/admin/leads/{lead_id}")
def put_lead(lead_id: int, payload: LeadIn, _: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, bool]:
    lead = session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    for field_name, value in payload.model_dump().items():
        setattr(lead, field_name, value)
    lead.updated_at = _now()
    session.add(lead)
    session.commit()
    return {"ok": True}


@app.get("/api/admin/products")
def get_products(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> list[ProductItem]:
    products = list(session.exec(select(ProductItem)).all())
    products.sort(key=lambda item: item.updated_at)
    return products


@app.put("/api/admin/products")
def put_products(items: list[ProductIn], _: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, bool]:
    for product in session.exec(select(ProductItem)).all():
        session.delete(product)
    session.commit()

    for item in items:
        session.add(ProductItem(**item.model_dump(), updated_at=_now()))
    session.commit()
    return {"ok": True}


@app.get("/api/admin/prompts")
def get_prompts(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> list[PromptCategory]:
    return list(session.exec(select(PromptCategory)).all())


@app.put("/api/admin/prompts")
def put_prompts(items: list[PromptIn], _: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, bool]:
    existing = {prompt.key: prompt for prompt in session.exec(select(PromptCategory)).all()}
    for item in items:
        if item.key in existing:
            prompt = existing[item.key]
            prompt.title = item.title
            prompt.content = item.content
            prompt.updated_at = _now()
            session.add(prompt)
        else:
            session.add(PromptCategory(key=item.key, title=item.title, content=item.content))
    session.commit()
    return {"ok": True}


@app.get("/api/admin/company")
def get_company(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> Optional[CompanyProfile]:
    return session.exec(select(CompanyProfile)).first()


@app.put("/api/admin/company")
def put_company(payload: CompanyIn, _: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, bool]:
    company = session.exec(select(CompanyProfile)).first()
    if not company:
        company = CompanyProfile(**payload.model_dump())
    else:
        for field_name, value in payload.model_dump().items():
            setattr(company, field_name, value)
    session.add(company)
    session.commit()
    return {"ok": True}


@app.get("/api/admin/scenarios")
def get_scenarios(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> list[Scenario]:
    return list(session.exec(select(Scenario)).all())


@app.put("/api/admin/scenarios")
def put_scenarios(items: list[ScenarioIn], _: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, bool]:
    for scenario in session.exec(select(Scenario)).all():
        session.delete(scenario)
    session.commit()

    for item in items:
        session.add(Scenario(**item.model_dump()))
    session.commit()
    return {"ok": True}


@app.get("/api/admin/scenario-templates")
def get_scenario_templates(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> list[ScenarioTemplate]:
    return list(session.exec(select(ScenarioTemplate).where(ScenarioTemplate.active == True)).all())


@app.post("/api/admin/scenario-templates/reset-defaults")
def reset_templates(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> dict[str, bool]:
    reset_default_scenario_templates(session)
    return {"ok": True}
