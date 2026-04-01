import json
import os
import re
from datetime import datetime
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .db import init_db, get_session, engine
from .models import (
    CompanyProfile,
    PromptCategory,
    Scenario,
    ChatSession,
    ChatMessage,
    ProductItem,
    Lead,
    ScenarioTemplate,
)
from .seed import seed_defaults

app = FastAPI(title="AgroLead API", version="1.0.0")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "315920")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "agrolead-admin-token")
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:0.5b")
MODEL_NUM_CTX = int(os.getenv("MODEL_NUM_CTX", "8192"))
MODEL_NUM_PREDICT = int(os.getenv("MODEL_NUM_PREDICT", "180"))


class LoginIn(BaseModel):
    username: str
    password: str


class ChatIn(BaseModel):
    text: str
    session_id: Optional[int] = None
    client_id: str = "web"


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


def build_system_prompt(session: Session) -> str:
    prompts = session.exec(select(PromptCategory)).all()
    company = session.exec(select(CompanyProfile)).first()
    content = "\n".join([f"[{p.key}] {p.content}" for p in prompts])
    company_block = (
        f"Компания: {company.name}\nАдрес: {company.address}\nТелефоны: {company.phones}\n"
        f"Email: {company.email}\nУслуги: {company.services}\nКонтакты:\n{company.contacts_markdown}"
        if company
        else ""
    )
    products = session.exec(
        select(ProductItem).where(ProductItem.active == True).order_by(ProductItem.stock_tons.desc())
    ).all()[:12]
    catalog = "\n".join([
        f"- {p.name}: {p.price_from:.0f}-{p.price_to:.0f} ₽/т, остаток {p.stock_tons:.0f} т, {p.location}" for p in products
    ])
    sales_policy = (
        "Твоя роль: заменить менеджера первичной линии продаж. "
        "Всегда веди диалог к квалификации лида: товар, класс, объем, регион, срок, контакт. "
        "Если данных не хватает — задавай 1 конкретный следующий вопрос. "
        "Не придумывай недоступные позиции и опирайся на каталог ниже."
    )
    return f"{content}\n\n{company_block}\n\n{sales_policy}\n\nКаталог:\n{catalog}"


def upsert_lead_from_text(session: Session, chat_session_id: int, text: str) -> None:
    s = text.lower()
    phone_match = re.search(r"(?:\+7|8)[\s\-\(\)]*\d[\d\s\-\(\)]{8,}", text)
    email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    vol_match = re.search(r"(\d+[\.,]?\d*)\s*(?:т|тонн|тонны|тонна)", s)

    product = ""
    if "пшен" in s:
        product = "Пшеница"
    elif "ячм" in s:
        product = "Ячмень"
    elif "кукуруз" in s:
        product = "Кукуруза"

    if not (phone_match or email_match or vol_match or product):
        return

    lead = session.exec(
        select(Lead).where(Lead.session_id == chat_session_id).order_by(Lead.created_at.desc())
    ).first()
    if not lead:
        lead = Lead(session_id=chat_session_id, source="chat", status="new")

    if phone_match:
        lead.phone = phone_match.group(0).strip()
    if email_match:
        lead.email = email_match.group(0).strip()
    if vol_match:
        lead.volume_tons = vol_match.group(1).replace(",", ".")
    if product and not lead.product:
        lead.product = product

    lead.updated_at = datetime.utcnow()
    session.add(lead)
    session.commit()


def guard_user_text(text: str) -> tuple[bool, str]:
    s = text.lower()
    blocked = ["ddos", "ддос", "взлом", "hack", "эксплойт", "ботнет", "malware", "xss", "rce"]
    if any(x in s for x in blocked):
        return False, "Я не помогаю с небезопасными запросами. Могу помочь только по зерновой продукции и оформлению заявки."

    allowed = [
        "привет", "здравствуйте", "добрый",
        "кто вы", "чем занимаетесь", "ассортимент", "пш", "ячм", "кукуруз", "зерн", "цена", "налич",
        "класс", "качест", "тонн", "объем", "объ", "достав", "логист", "отгруз", "заявк", "контакт",
    ]
    if not any(x in s for x in allowed):
        return False, "Я консультирую только по продукции, цене, наличию, логистике и заявкам ООО «Петрохлеб-Кубань»."

    return True, ""


def quick_reply(text: str) -> Optional[str]:
    s = text.lower().strip()

    if any(x in s for x in ["привет", "здравствуйте", "добрый"]):
        return (
            "Здравствуйте! Я ассистент ООО «Петрохлеб-Кубань». "
            "Подскажу по наличию, цене, логистике и оформлению заявки. "
            "Укажите, пожалуйста, товар, класс и ориентировочный объем."
        )

    if "кто вы" in s or "чем занимаетесь" in s:
        return (
            "ООО «Петрохлеб-Кубань» занимается закупкой, хранением, логистикой, продажей зерновой продукции и ВЭД. "
            "Могу помочь по наличию, цене и срокам отгрузки. Какой товар вас интересует?"
        )

    if ("цена" in s or "стоим" in s) and ("объем" in s or "объ" in s or "миним" in s):
        return (
            "Цена и минимальный объем зависят от культуры, класса качества, объема партии и точки поставки. "
            "Напишите, пожалуйста: товар, класс, объем (тонны) и регион доставки — дам ориентир и передам заявку менеджеру."
        )

    if "товар" in s or "ассортимент" in s or "налич" in s:
        return (
            "По профилю компании работаем с зерновыми культурами: пшеница, ячмень, кукуруза. "
            "Актуальное наличие и условия зависят от класса и объема партии. "
            "Уточните, пожалуйста, культуру, класс и объем в тоннах."
        )

    return None


def sanitize_assistant_text(text: str) -> str:
    cleaned = text
    cleaned = cleaned.replace("Клиент:", "").replace("Ассистент:", "")
    lines = []
    for line in cleaned.splitlines():
        l = line.strip()
        if l.lower().startswith(("клиент:", "ассистент:", "запрос:", "ответ:")):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()

    banned = ["сухофрукт", "бобов", "ягод", "мяс", "овощ", "рецепт", "декоратив"]
    if any(x in cleaned.lower() for x in banned):
        return (
            "По профилю ООО «Петрохлеб-Кубань» консультирую только по зерновой продукции "
            "(пшеница, ячмень, кукуруза), логистике и оформлению заявки. "
            "Уточните культуру, класс и объем в тоннах."
        )

    if len(cleaned) > 700:
        cleaned = cleaned[:700].rsplit(" ", 1)[0] + "..."

    return cleaned


@app.on_event("startup")
def startup() -> None:
    init_db()
    with Session(engine) as session:
        seed_defaults(session)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/api/public/bootstrap")
def bootstrap(session: Session = Depends(get_session)):
    company = session.exec(select(CompanyProfile)).first()
    scenarios = session.exec(select(Scenario).where(Scenario.active == True)).all()
    products = session.exec(
        select(ProductItem).where(ProductItem.active == True).order_by(ProductItem.stock_tons.desc())
    ).all()[:8]
    return {
        "company": company,
        "scenarios": scenarios,
        "products": products,
        "model": MODEL_NAME,
    }


@app.get("/api/public/catalog")
def public_catalog(session: Session = Depends(get_session), limit: int = 100):
    return session.exec(
        select(ProductItem).where(ProductItem.active == True).order_by(ProductItem.updated_at.desc())
    ).all()[:limit]


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatIn, session: Session = Depends(get_session)):
    ok, reject = guard_user_text(payload.text)
    chat_session = None
    if payload.session_id:
        chat_session = session.get(ChatSession, payload.session_id)
    if not chat_session:
        chat_session = ChatSession(client_id=payload.client_id or str(uuid4()))
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)

    session.add(ChatMessage(session_id=chat_session.id, role="user", text=payload.text, blocked=not ok, reason="blocked" if not ok else ""))
    session.commit()
    upsert_lead_from_text(session, chat_session.id, payload.text)

    fast = quick_reply(payload.text)
    if fast:
        def fast_gen():
            yield json.dumps({"session_id": chat_session.id, "token": fast, "done": True}) + "\n"

        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=fast))
        session.commit()
        return StreamingResponse(fast_gen(), media_type="application/x-ndjson")

    if not ok:
        def reject_gen():
            yield json.dumps({"session_id": chat_session.id, "token": reject, "done": True}) + "\n"

        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=reject, blocked=True, reason="policy"))
        session.commit()
        return StreamingResponse(reject_gen(), media_type="application/x-ndjson")

    recent = session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat_session.id).order_by(ChatMessage.created_at.desc())
    ).all()[:6]
    history = "\n".join([
        f"{'Клиент' if m.role == 'user' else 'Ассистент'}: {m.text}" for m in reversed(recent)
    ])

    prompt = f"{build_system_prompt(session)}\n\n{history}\nАссистент:"

    async def gen():
        assistant_full = ""
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE}/api/generate",
                    json={
                        "model": MODEL_NAME,
                        "prompt": prompt,
                        "stream": True,
                        "keep_alive": "45m",
                        "options": {
                            "temperature": 0.05,
                            "num_predict": MODEL_NUM_PREDICT,
                            "repeat_penalty": 1.1,
                            "num_ctx": MODEL_NUM_CTX,
                        },
                    },
                ) as r:
                    if r.status_code >= 400:
                        raise HTTPException(status_code=502, detail=f"LLM upstream error: HTTP {r.status_code}")

                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        try:
                            part = json.loads(line)
                        except Exception:
                            continue
                        token = part.get("response", "")
                        if token:
                            assistant_full += token
                            yield json.dumps({"session_id": chat_session.id, "token": token, "done": False}) + "\n"
                        if part.get("done"):
                            clean = sanitize_assistant_text(assistant_full)
                            with Session(engine) as write_session:
                                write_session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=clean))
                                write_session.commit()
                            yield json.dumps({"session_id": chat_session.id, "token": "", "done": True}) + "\n"
                            break
        except Exception:
            fallback = (
                "Сервис генерации временно недоступен. "
                "Уточните, пожалуйста, товар, класс и объем в тоннах — передам заявку менеджеру."
            )
            with Session(engine) as write_session:
                write_session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=fallback, blocked=True, reason="llm_unavailable"))
                write_session.commit()
            yield json.dumps({"session_id": chat_session.id, "token": fallback, "done": True}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/chat")
async def chat(payload: ChatIn, session: Session = Depends(get_session)):
    ok, reject = guard_user_text(payload.text)
    chat_session = None
    if payload.session_id:
        chat_session = session.get(ChatSession, payload.session_id)
    if not chat_session:
        chat_session = ChatSession(client_id=payload.client_id or str(uuid4()))
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)

    session.add(ChatMessage(session_id=chat_session.id, role="user", text=payload.text, blocked=not ok, reason="blocked" if not ok else ""))
    session.commit()
    upsert_lead_from_text(session, chat_session.id, payload.text)

    fast = quick_reply(payload.text)
    if fast:
        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=fast))
        session.commit()
        return {"session_id": chat_session.id, "text": fast, "done": True}

    if not ok:
        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=reject, blocked=True, reason="policy"))
        session.commit()
        return {"session_id": chat_session.id, "text": reject, "done": True}

    recent = session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat_session.id).order_by(ChatMessage.created_at.desc())
    ).all()[:6]
    history = "\n".join([
        f"{'Клиент' if m.role == 'user' else 'Ассистент'}: {m.text}" for m in reversed(recent)
    ])
    prompt = f"{build_system_prompt(session)}\n\n{history}\nАссистент:"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{OLLAMA_BASE}/api/generate",
                json={
                    "model": MODEL_NAME,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "45m",
                    "options": {
                        "temperature": 0.05,
                        "num_predict": MODEL_NUM_PREDICT,
                        "repeat_penalty": 1.1,
                        "num_ctx": MODEL_NUM_CTX,
                    },
                },
            )
            r.raise_for_status()
            data = r.json()
            text = sanitize_assistant_text(data.get("response", "").strip())
            if not text:
                text = "Уточните, пожалуйста, товар, класс и объем в тоннах — передам заявку менеджеру."
            session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=text))
            session.commit()
            return {"session_id": chat_session.id, "text": text, "done": True}
    except Exception:
        fallback = (
            "Сервис генерации временно недоступен. "
            "Уточните, пожалуйста, товар, класс и объем в тоннах — передам заявку менеджеру."
        )
        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=fallback, blocked=True, reason="llm_unavailable"))
        session.commit()
        return {"session_id": chat_session.id, "text": fallback, "done": True}


@app.post("/api/admin/login")
def admin_login(payload: LoginIn):
    if payload.username != ADMIN_USER or payload.password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="Bad credentials")
    return {"token": ADMIN_TOKEN}


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


@app.get("/api/admin/stats")
def admin_stats(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    sessions = session.exec(select(ChatSession)).all()
    messages = session.exec(select(ChatMessage)).all()
    blocked = len([m for m in messages if m.blocked])
    leads = len([m for m in messages if m.role == "assistant" and any(x in m.text.lower() for x in ["контакт", "телефон", "email", "почт"])])
    recent = session.exec(select(ChatSession).order_by(ChatSession.created_at.desc())).all()[:20]
    leads_total = len(session.exec(select(Lead)).all())
    leads_new = len(session.exec(select(Lead).where(Lead.status == "new")).all())
    return {
        "sessions": len(sessions),
        "messages": len(messages),
        "blocked": blocked,
        "lead_signals": leads,
        "leads_total": leads_total,
        "leads_new": leads_new,
        "recent_sessions": recent,
    }


@app.get("/api/admin/chats")
def admin_chats(_: None = Depends(require_admin), session: Session = Depends(get_session), limit: int = 100):
    msgs = session.exec(select(ChatMessage).order_by(ChatMessage.created_at.desc())).all()[:limit]
    return list(reversed(msgs))


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


@app.get("/api/admin/scenario-templates")
def get_scenario_templates(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    return session.exec(select(ScenarioTemplate).where(ScenarioTemplate.active == True)).all()


@app.post("/api/admin/scenario-templates/reset-defaults")
def reset_scenario_templates(_: None = Depends(require_admin), session: Session = Depends(get_session)):
    old = session.exec(select(ScenarioTemplate)).all()
    for t in old:
        session.delete(t)
    session.commit()
    seed_defaults(session)
    return {"ok": True}

