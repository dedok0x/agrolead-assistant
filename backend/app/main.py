import json
import os
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .db import engine, get_session, init_db
from .guardrails import evaluate_guardrails
from .llm_service import LLMService
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
from .nanoclaw_adapter import NanoClawAdapter
from .sales_logic import next_qualification_question, sync_state_and_lead
from .seed import seed_defaults

app = FastAPI(title="AgroLead API (NanoClaw)", version="2.0.0")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "315920")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "agrolead-admin-token")

llm_service = LLMService()
nanoclaw = NanoClawAdapter()


class LoginIn(BaseModel):
    username: str
    password: str


class ChatIn(BaseModel):
    text: str
    session_id: Optional[int] = None
    client_id: str = "web"


class ChatDryRunIn(BaseModel):
    text: str


class NanoClawAgentIn(BaseModel):
    text: str
    context: str = ""


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
    status: str = "new"
    source: str = "chat"
    comment: str = ""


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def sales_system_prompt(session: Session) -> str:
    company = session.exec(select(CompanyProfile)).first()
    products = session.exec(select(ProductItem).where(ProductItem.active == True).order_by(ProductItem.stock_tons.desc())).all()[:10]

    company_block = (
        f"Компания: {company.name}\n"
        f"Адрес: {company.address}\n"
        f"Телефоны: {company.phones}\n"
        f"Email: {company.email}\n"
        if company
        else ""
    )

    product_lines = [
        f"- {p.name}: {p.price_from:.0f}-{p.price_to:.0f} ₽/т, остаток {p.stock_tons:.0f} т, {p.location}"
        for p in products
    ]
    catalog = "\n".join(product_lines)
    return (
        "Ты sales-ассистент ООО «Петрохлеб-Кубань». Стиль: живой, прямой, по-кубански. "
        "Не выдумывай остатки и цены. Всегда дожимай квалификацию: товар, класс, объем, регион, срок, контакт. "
        "Если клиент токсичен — ответ короткий и без продолжения вежливой анкеты.\n\n"
        f"{company_block}\nКаталог:\n{catalog}"
    )


def sanitize_text(text: str) -> str:
    cleaned = (text or "").replace("Ассистент:", "").replace("Клиент:", "").strip()
    if len(cleaned) > 700:
        cleaned = cleaned[:700].rsplit(" ", 1)[0] + "..."
    return cleaned


def get_or_create_chat_session(session: Session, payload: ChatIn) -> ChatSession:
    chat_session = session.get(ChatSession, payload.session_id) if payload.session_id else None
    if chat_session:
        return chat_session
    chat_session = ChatSession(client_id=payload.client_id or str(uuid4()))
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session


async def process_chat(session: Session, payload: ChatIn) -> dict:
    chat_session = get_or_create_chat_session(session, payload)
    session.add(ChatMessage(session_id=chat_session.id, role="user", text=payload.text))
    session.commit()

    guard = evaluate_guardrails(payload.text)
    state = sync_state_and_lead(session, chat_session.id, payload.text)

    if not guard.allowed:
        if guard.toxicity_level > 0:
            state.toxicity_level = guard.toxicity_level
            state.state = "stopped_toxic"
            session.add(state)
            session.commit()
        session.add(
            ChatMessage(
                session_id=chat_session.id,
                role="assistant",
                text=guard.answer,
                blocked=True,
                reason=guard.reason,
                provider="guardrails",
                model_used="rule-based",
            )
        )
        session.commit()
        return {
            "session_id": chat_session.id,
            "done": True,
            "text": guard.answer,
            "provider": "guardrails",
            "model": "rule-based",
            "state": state.state,
        }

    if state.state != "qualified":
        q = next_qualification_question(state)
        state.last_question = q
        session.add(state)
        session.add(
            ChatMessage(
                session_id=chat_session.id,
                role="assistant",
                text=q,
                reason="qualification_state_machine",
                provider="state-machine",
                model_used="rule-based",
            )
        )
        session.commit()
        return {
            "session_id": chat_session.id,
            "done": True,
            "text": q,
            "provider": "state-machine",
            "model": "rule-based",
            "state": state.state,
        }

    recent = session.exec(select(ChatMessage).where(ChatMessage.session_id == chat_session.id).order_by(ChatMessage.created_at.desc())).all()[:8]
    history = "\n".join([f"{'Клиент' if m.role == 'user' else 'Ассистент'}: {m.text}" for m in reversed(recent)])

    nano_context = {
        "session_id": chat_session.id,
        "history": history,
        "state": {
            "product": state.product,
            "grade": state.grade,
            "volume_tons": state.volume_tons,
            "region": state.region,
            "delivery_term": state.delivery_term,
            "contact": state.contact,
        },
        "system_prompt": sales_system_prompt(session),
    }

    try:
        nano_response = await nanoclaw.chat(message=payload.text, context=nano_context)
        text = sanitize_text(nano_response["text"]) or "Принял. Передаю менеджеру, он закрепит условия."
        provider = nano_response.get("provider", "nanoclaw")
        model = nano_response.get("model", "nanoclaw-runtime")
    except Exception:
        llm_prompt = f"{history}\nСобранный лид: товар={state.product}, класс={state.grade}, объем={state.volume_tons}, регион={state.region}, срок={state.delivery_term}, контакт={state.contact}"
        text, provider, model = await llm_service.complete(system_prompt=sales_system_prompt(session), user_prompt=llm_prompt, reason="nanoclaw_fallback")
        text = sanitize_text(text)

    session.add(
        ChatMessage(
            session_id=chat_session.id,
            role="assistant",
            text=text,
            reason="qualified_offer",
            provider=provider,
            model_used=model,
        )
    )
    session.commit()
    return {
        "session_id": chat_session.id,
        "done": True,
        "text": text,
        "provider": provider,
        "model": model,
        "state": state.state,
    }


@app.on_event("startup")
def startup() -> None:
    init_db()
    with Session(engine) as session:
        seed_defaults(session)


@app.on_event("shutdown")
async def shutdown() -> None:
    await llm_service.close()
    await nanoclaw.close()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.utcnow().isoformat(), "agent_engine": "nanoclaw"}


@app.get("/api/llm/status")
def llm_status() -> dict:
    return llm_service.status()


@app.post("/api/chat")
async def chat(payload: ChatIn, session: Session = Depends(get_session)):
    if not (payload.text or "").strip():
        raise HTTPException(status_code=400, detail="text is required")
    return await process_chat(session=session, payload=payload)


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatIn, session: Session = Depends(get_session)):
    result = await process_chat(session=session, payload=payload)

    def gen():
        yield json.dumps({"session_id": result["session_id"], "token": result["text"], "done": True}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/chat/dry-run")
async def chat_dry_run(payload: ChatDryRunIn, session: Session = Depends(get_session)):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    guard = evaluate_guardrails(text)
    if not guard.allowed:
        return {"done": True, "provider": "guardrails", "model": "rule-based", "text": guard.answer}

    system_prompt = sales_system_prompt(session)
    response, provider, model = await llm_service.complete(system_prompt=system_prompt, user_prompt=f"Клиент: {text}", reason="dry-run")
    return {"done": True, "provider": provider, "model": model, "text": sanitize_text(response)}


@app.post("/api/nanoclaw/agent/chat")
async def nanoclaw_agent_chat(payload: NanoClawAgentIn, session: Session = Depends(get_session)):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    guard = evaluate_guardrails(text)
    if not guard.allowed:
        return {"done": True, "provider": "guardrails", "model": "rule-based", "text": guard.answer}

    llm_input = f"Контекст от NanoClaw: {payload.context}\nЗапрос: {text}\nДай короткий ответ продажника по зерну."
    text_out, provider, model = await llm_service.complete(
        system_prompt=sales_system_prompt(session),
        user_prompt=llm_input,
        reason="nanoclaw-adapter",
    )
    return {"done": True, "provider": provider, "model": model, "text": sanitize_text(text_out)}


@app.post("/api/picoclaw/agent/chat")
async def picoclaw_compat(payload: NanoClawAgentIn, session: Session = Depends(get_session)):
    result = await nanoclaw_agent_chat(payload=payload, session=session)
    result["deprecated"] = True
    return result


@app.post("/api/admin/login")
def admin_login(payload: LoginIn):
    if payload.username != ADMIN_USER or payload.password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="Bad credentials")
    return {"token": ADMIN_TOKEN}


@app.get("/api/public/bootstrap")
def bootstrap(session: Session = Depends(get_session)):
    return {
        "company": session.exec(select(CompanyProfile)).first(),
        "scenarios": session.exec(select(Scenario).where(Scenario.active == True)).all(),
        "products": session.exec(select(ProductItem).where(ProductItem.active == True).order_by(ProductItem.stock_tons.desc())).all()[:8],
        "llm": llm_service.status(),
    }


@app.get("/api/admin/stats")
def admin_stats(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    sessions = session.exec(select(ChatSession)).all()
    messages = session.exec(select(ChatMessage)).all()
    leads_total = len(session.exec(select(Lead)).all())
    leads_new = len(session.exec(select(Lead).where(Lead.status == "new")).all())
    states = session.exec(select(ConversationState)).all()
    return {
        "sessions": len(sessions),
        "messages": len(messages),
        "leads_total": leads_total,
        "leads_new": leads_new,
        "state_machine": {
            "total": len(states),
            "qualified": len([s for s in states if s.state == "qualified"]),
            "stopped_toxic": len([s for s in states if s.state == "stopped_toxic"]),
        },
        "llm_usage": llm_service.status(),
    }


@app.get("/api/admin/chats")
def admin_chats(_: None = Depends(require_admin), session: Session = Depends(get_session), limit: int = 120):
    msgs = session.exec(select(ChatMessage).order_by(ChatMessage.created_at.desc())).all()[:limit]
    return list(reversed(msgs))


@app.get("/api/admin/leads")
def get_leads(_: None = Depends(require_admin), session: Session = Depends(get_session), limit: int = 200):
    return session.exec(select(Lead).order_by(Lead.updated_at.desc())).all()[:limit]


@app.post("/api/admin/leads")
def post_lead(payload: LeadIn, _: None = Depends(require_admin), session: Session = Depends(get_session)):
    lead = Lead(**payload.model_dump(), created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


@app.put("/api/admin/leads/{lead_id}")
def put_lead(lead_id: int, payload: LeadIn, _: None = Depends(require_admin), session: Session = Depends(get_session)):
    lead = session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    for k, v in payload.model_dump().items():
        setattr(lead, k, v)
    lead.updated_at = datetime.utcnow()
    session.add(lead)
    session.commit()
    return {"ok": True}


@app.get("/api/admin/products")
def get_products(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return session.exec(select(ProductItem).order_by(ProductItem.updated_at.desc())).all()


@app.put("/api/admin/products")
def put_products(items: list[ProductIn], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    old = session.exec(select(ProductItem)).all()
    for p in old:
        session.delete(p)
    session.commit()
    for i in items:
        session.add(ProductItem(**i.model_dump(), updated_at=datetime.utcnow()))
    session.commit()
    return {"ok": True}


@app.get("/api/admin/prompts")
def get_prompts(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return session.exec(select(PromptCategory)).all()


@app.put("/api/admin/prompts")
def put_prompts(items: list[PromptIn], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    existing = {p.key: p for p in session.exec(select(PromptCategory)).all()}
    for item in items:
        if item.key in existing:
            p = existing[item.key]
            p.title = item.title
            p.content = item.content
            p.updated_at = datetime.utcnow()
            session.add(p)
        else:
            session.add(PromptCategory(key=item.key, title=item.title, content=item.content))
    session.commit()
    return {"ok": True}


@app.get("/api/admin/company")
def get_company(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return session.exec(select(CompanyProfile)).first()


@app.put("/api/admin/company")
def put_company(payload: CompanyIn, _: None = Depends(require_admin), session: Session = Depends(get_session)):
    company = session.exec(select(CompanyProfile)).first()
    if not company:
        company = CompanyProfile(**payload.model_dump())
    else:
        for k, v in payload.model_dump().items():
            setattr(company, k, v)
    session.add(company)
    session.commit()
    return {"ok": True}


@app.get("/api/admin/scenarios")
def get_scenarios(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return session.exec(select(Scenario)).all()


@app.put("/api/admin/scenarios")
def put_scenarios(items: list[ScenarioIn], _: None = Depends(require_admin), session: Session = Depends(get_session)):
    old = session.exec(select(Scenario)).all()
    for s in old:
        session.delete(s)
    session.commit()
    for i in items:
        session.add(Scenario(**i.model_dump()))
    session.commit()
    return {"ok": True}


@app.get("/api/admin/scenario-templates")
def get_scenario_templates(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return session.exec(select(ScenarioTemplate).where(ScenarioTemplate.active == True)).all()

