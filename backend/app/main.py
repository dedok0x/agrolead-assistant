import json
import os
from datetime import datetime
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .db import init_db, get_session, engine
from .models import CompanyProfile, PromptCategory, Scenario, ChatSession, ChatMessage
from .seed import seed_defaults

app = FastAPI(title="AgroLead API", version="1.0.0")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "315920")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "agrolead-admin-token")
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:0.5b")


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
    return f"{content}\n\n{company_block}"


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
            "Работаем по зерновым культурам (в т.ч. пшеница, ячмень, кукуруза). "
            "Чтобы проверить актуальное наличие, уточните культуру, класс и нужный объем."
        )

    return None


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
    return {
        "company": company,
        "scenarios": scenarios,
        "model": MODEL_NAME,
    }


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
    ).all()[:12]
    history = "\n".join([
        f"{'Клиент' if m.role == 'user' else 'Ассистент'}: {m.text}" for m in reversed(recent)
    ])

    prompt = f"{build_system_prompt(session)}\n\n{history}\nКлиент: {payload.text}\nАссистент:"

    async def gen():
        assistant_full = ""
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_BASE}/api/generate",
                json={
                    "model": MODEL_NAME,
                    "prompt": prompt,
                    "stream": True,
                    "keep_alive": "45m",
                    "options": {"temperature": 0.05, "num_predict": 90, "repeat_penalty": 1.1},
                },
            ) as r:
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
                        clean = assistant_full.replace("Клиент:", "").replace("Ассистент:", "").strip()
                        with Session(engine) as write_session:
                            write_session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=clean))
                            write_session.commit()
                        yield json.dumps({"session_id": chat_session.id, "token": "", "done": True}) + "\n"
                        break

    return StreamingResponse(gen(), media_type="application/x-ndjson")


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
    return {
        "sessions": len(sessions),
        "messages": len(messages),
        "blocked": blocked,
        "lead_signals": leads,
        "recent_sessions": recent,
    }


@app.get("/api/admin/chats")
def admin_chats(_: None = Depends(require_admin), session: Session = Depends(get_session), limit: int = 100):
    msgs = session.exec(select(ChatMessage).order_by(ChatMessage.created_at.desc())).all()[:limit]
    return list(reversed(msgs))

