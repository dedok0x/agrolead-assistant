import json
import os
import re
import time
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
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()

GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_OAUTH_URL = os.getenv("GIGACHAT_OAUTH_URL", "https://gigachat.devices.sberbank.ru/api/v2/oauth")
GIGACHAT_API_URL = os.getenv("GIGACHAT_API_URL", "https://gigachat.devices.sberbank.ru/api/v1")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2")
GIGACHAT_VERIFY_SSL = os.getenv("GIGACHAT_VERIFY_SSL", "1") not in {"0", "false", "False"}
GIGACHAT_TOKEN_REFRESH_SECONDS = int(os.getenv("GIGACHAT_TOKEN_REFRESH_SECONDS", "1500"))
LLM_REQUEST_TIMEOUT_SECONDS = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45"))

_gigachat_token: Optional[str] = None
_gigachat_token_expire_ts: float = 0.0
_gigachat_token_issued_ts: float = 0.0

_http_limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
_http_timeout = httpx.Timeout(LLM_REQUEST_TIMEOUT_SECONDS, connect=5.0)
_ollama_client = httpx.AsyncClient(timeout=_http_timeout, limits=_http_limits)
_gigachat_client = httpx.AsyncClient(timeout=_http_timeout, limits=_http_limits, verify=GIGACHAT_VERIFY_SSL)

_llm_usage_stats = {
    "gigachat": 0,
    "ollama": 0,
    "fallback": 0,
    "scripted_sales": 0,
    "quick_reply": 0,
}
_last_provider = "unknown"


def mark_provider(provider: str) -> None:
    global _last_provider
    if provider not in _llm_usage_stats:
        _llm_usage_stats[provider] = 0
    _llm_usage_stats[provider] += 1
    _last_provider = provider


class LoginIn(BaseModel):
    username: str
    password: str


class ChatIn(BaseModel):
    text: str
    session_id: Optional[int] = None
    client_id: str = "web"


class ChatDryRunIn(BaseModel):
    text: str


class PicoclawAgentIn(BaseModel):
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
    grade_match = re.search(r"([1-6])\s*класс", s)

    region = ""
    m_region = re.search(r"(?:в|до|по)\s+([А-Яа-яA-Za-z\-\s]{3,40})", text)
    if m_region:
        region = m_region.group(1).strip(" .,")

    delivery_term = ""
    if any(x in s for x in ["срочно", "сегодня", "завтра"]):
        delivery_term = "срочно"
    elif any(x in s for x in ["недел", "месяц", "дата", "срок"]):
        delivery_term = text.strip()[:120]

    product = ""
    if "пшен" in s:
        product = "Пшеница"
    elif "ячм" in s:
        product = "Ячмень"
    elif "кукуруз" in s:
        product = "Кукуруза"

    client_name = ""
    m_name = re.search(r"(?:меня зовут|я\s+)\s+([А-Яа-яA-Za-z\-]{3,40})", text)
    if m_name:
        client_name = m_name.group(1).strip()
    elif re.fullmatch(r"[А-Яа-яA-Za-z\-]{3,40}", text.strip()):
        one_word = text.strip()
        known_regions = {"краснодар", "самара", "самарская", "москва", "ростов", "казань", "воронеж"}
        if one_word.lower() in known_regions:
            region = one_word
        else:
            client_name = one_word

    if not (phone_match or email_match or vol_match or product or grade_match or region or delivery_term or client_name):
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
    if grade_match:
        lead.grade = f"{grade_match.group(1)} класс"
    if region and not lead.region:
        lead.region = region
    if delivery_term and not lead.delivery_term:
        lead.delivery_term = delivery_term
    if client_name and not lead.client_name:
        lead.client_name = client_name

    if lead.product and lead.volume_tons and (lead.phone or lead.email):
        lead.status = "qualified"
    elif any([lead.product, lead.grade, lead.volume_tons, lead.region, lead.delivery_term, lead.client_name]):
        lead.status = "in_progress"

    lead.updated_at = datetime.utcnow()
    session.add(lead)
    session.commit()


def guard_user_text(text: str) -> tuple[bool, str]:
    s = text.lower()
    blocked = ["ddos", "ддос", "взлом", "hack", "эксплойт", "ботнет", "malware", "xss", "rce"]
    if any(x in s for x in blocked):
        return False, "Я не помогаю с небезопасными запросами. Могу помочь только по зерновой продукции и оформлению заявки."

    return True, ""


def quick_reply(text: str) -> Optional[str]:
    s = text.lower().strip()

    if any(x in s for x in ["чё как", "че как", "как дела", "как ты"]):
        return (
            "На связи, всё отлично. Помогу быстро с подбором зерновой продукции и условий поставки. "
            "Что хотите рассчитать в первую очередь: цену, наличие или логистику?"
        )

    if "просто пообщаться" in s:
        return (
            "С удовольствием на связи. Я всё же заточен под продажи зерновых, поэтому могу быть максимально полезен по цене, наличию и доставке. "
            "С какой культуры начнем?"
        )

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


def _find_product_offer(session: Session, product: str, grade: str = "") -> Optional[ProductItem]:
    items = session.exec(
        select(ProductItem).where(ProductItem.active == True).order_by(ProductItem.stock_tons.desc())
    ).all()
    p = (product or "").lower()
    g = (grade or "").lower()
    for it in items:
        text = f"{it.name} {it.culture} {it.grade}".lower()
        if p and p not in text:
            continue
        if g and g not in text:
            continue
        return it
    return None


def _next_question_for_lead(lead: Optional[Lead]) -> str:
    if not lead:
        return "Подскажите, пожалуйста, какую культуру рассматриваете и какой ориентировочный объем в тоннах?"
    if not lead.product:
        return "Подскажите, пожалуйста, какую культуру вы рассматриваете: пшеница, ячмень или кукуруза?"
    if not lead.grade:
        return "Уточните, пожалуйста, класс/качество продукции."
    if not lead.volume_tons:
        return "Какой ориентировочный объем партии в тоннах вам нужен?"
    if not lead.region:
        return "В какой регион нужна поставка?"
    if not (lead.phone or lead.email):
        return "Оставьте, пожалуйста, телефон или email, чтобы менеджер закрепил цену и условия."
    return "Если удобно, могу сразу передать заявку менеджеру и зафиксировать условия."


def scripted_sales_reply(session: Session, chat_session_id: int, text: str) -> Optional[str]:
    s = text.lower().strip()
    lead = session.exec(
        select(Lead).where(Lead.session_id == chat_session_id).order_by(Lead.updated_at.desc())
    ).first()

    if not s:
        return "Я на связи. Подскажите, пожалуйста, что именно интересует по зерновым: цена, наличие или логистика?"

    if re.fullmatch(r"\d+", s):
        return "Понял вас. Чтобы дать точный ответ, подскажите, пожалуйста, товар, класс и объем в тоннах."

    if "на какой модели" in s or "какая модель" in s:
        return "Я работаю как корпоративный ассистент отдела продаж и помогаю по наличию, цене, логистике и оформлению заявки. Давайте подберем условия под ваш запрос."

    if "не хочу" in s and any(x in s for x in ["класс", "пшениц", "ячмен", "кукуруз"]):
        if lead:
            if "пшениц" in s:
                lead.product = ""
                lead.grade = ""
            elif "ячмен" in s:
                lead.product = ""
                lead.grade = ""
            elif "кукуруз" in s:
                lead.product = ""
                lead.grade = ""
            session.add(lead)
            session.commit()
        return "Принято, скорректируем запрос. Какую культуру и класс тогда рассматриваете вместо этого варианта?"

    if any(x in s for x in ["какие товары", "ассортимент", "что в наличии", "наличие"]):
        products = session.exec(
            select(ProductItem).where(ProductItem.active == True).order_by(ProductItem.stock_tons.desc())
        ).all()[:4]
        if not products:
            return "Сейчас уточняем актуальные остатки. Подскажите, какая культура вас интересует, и я зафиксирую запрос менеджеру."
        items = "; ".join([f"{p.name} ({p.price_from:.0f}-{p.price_to:.0f} ₽/т)" for p in products])
        return f"Сейчас в работе по каталогу: {items}. Что из этого интересно вам по культуре и объему?"

    if any(x in s for x in ["цена", "стоимость", "минимальный объем", "минимум"]):
        if lead and lead.product:
            offer = _find_product_offer(session, lead.product, lead.grade)
            if offer:
                return (
                    f"По {offer.name} ориентир {offer.price_from:.0f}-{offer.price_to:.0f} ₽/т, доступный остаток около {offer.stock_tons:.0f} т. "
                    f"{_next_question_for_lead(lead)}"
                )
        return "Ориентир по цене и минимальному объему зависит от культуры, класса и точки поставки. Уточните, пожалуйста, товар, класс и объем в тоннах."

    sales_tokens = ["пшениц", "ячмен", "кукуруз", "класс", "тонн", "объем", "достав", "логист", "заявк", "краснодар", "цена"]
    if any(x in s for x in sales_tokens):
        if lead and lead.product:
            offer = _find_product_offer(session, lead.product, lead.grade)
            if offer:
                return (
                    f"Принял запрос: {offer.name}. По текущему ориентиру это {offer.price_from:.0f}-{offer.price_to:.0f} ₽/т, "
                    f"остаток около {offer.stock_tons:.0f} т, отгрузка из региона {offer.location}. "
                    f"{_next_question_for_lead(lead)}"
                )
        return _next_question_for_lead(lead)

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


def should_try_gigachat() -> bool:
    if LLM_PROVIDER == "ollama":
        return False
    if LLM_PROVIDER == "gigachat":
        return True
    return bool(GIGACHAT_AUTH_KEY)


async def get_gigachat_token(client: httpx.AsyncClient) -> str:
    global _gigachat_token, _gigachat_token_expire_ts, _gigachat_token_issued_ts
    now = time.time()
    if (
        _gigachat_token
        and (now - _gigachat_token_issued_ts) < GIGACHAT_TOKEN_REFRESH_SECONDS
        and now < (_gigachat_token_expire_ts - 60)
    ):
        return _gigachat_token

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid4()),
        "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
    }
    data = {"scope": GIGACHAT_SCOPE}
    resp = await client.post(GIGACHAT_OAUTH_URL, headers=headers, data=data)
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("GigaChat token not found")

    expires_at = payload.get("expires_at")
    if isinstance(expires_at, (int, float)) and expires_at > 10_000_000_000:
        _gigachat_token_expire_ts = float(expires_at) / 1000.0
    elif isinstance(expires_at, (int, float)):
        _gigachat_token_expire_ts = float(expires_at)
    else:
        _gigachat_token_expire_ts = now + 25 * 60

    _gigachat_token = token
    _gigachat_token_issued_ts = now
    return token


async def generate_via_gigachat(prompt: str) -> str:
    if not GIGACHAT_AUTH_KEY:
        raise RuntimeError("GIGACHAT_AUTH_KEY is empty")

    token = await get_gigachat_token(_gigachat_client)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "model": GIGACHAT_MODEL,
        "messages": [
            {"role": "system", "content": "Ты корпоративный ассистент ООО «Петрохлеб-Кубань»."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.05,
        "max_tokens": MODEL_NUM_PREDICT,
    }
    resp = await _gigachat_client.post(f"{GIGACHAT_API_URL}/chat/completions", headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("GigaChat returned empty choices")
    msg = choices[0].get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text:
        raise RuntimeError("GigaChat returned empty content")
    return text


async def generate_via_ollama(prompt: str) -> str:
    r = await _ollama_client.post(
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
    return (data.get("response") or "").strip()


async def generate_llm_response(prompt: str) -> tuple[str, str]:
    if should_try_gigachat():
        try:
            return await generate_via_gigachat(prompt), "gigachat"
        except Exception:
            if LLM_PROVIDER == "gigachat":
                raise

    text = await generate_via_ollama(prompt)
    return text, "ollama"


@app.on_event("startup")
def startup() -> None:
    init_db()
    with Session(engine) as session:
        seed_defaults(session)


@app.on_event("shutdown")
async def shutdown() -> None:
    await _ollama_client.aclose()
    await _gigachat_client.aclose()


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/api/llm/status")
def llm_status():
    if should_try_gigachat():
        return {"mode": LLM_PROVIDER, "active": "gigachat", "fallback": "ollama", "model": GIGACHAT_MODEL, "usage": _llm_usage_stats, "last_provider": _last_provider}
    return {"mode": LLM_PROVIDER, "active": "ollama", "fallback": None, "model": MODEL_NAME, "usage": _llm_usage_stats, "last_provider": _last_provider}


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

    chat_session_id = chat_session.id

    session.add(ChatMessage(session_id=chat_session.id, role="user", text=payload.text, blocked=not ok, reason="blocked" if not ok else ""))
    session.commit()
    upsert_lead_from_text(session, chat_session.id, payload.text)

    fast = quick_reply(payload.text)
    if fast:
        def fast_gen():
            yield json.dumps({"session_id": chat_session_id, "token": fast, "done": True}) + "\n"

        mark_provider("quick_reply")
        session.add(ChatMessage(session_id=chat_session_id, role="assistant", text=fast, reason="quick_reply"))
        session.commit()
        return StreamingResponse(fast_gen(), media_type="application/x-ndjson")

    scripted = scripted_sales_reply(session, chat_session_id, payload.text)
    if scripted:
        def scripted_gen():
            yield json.dumps({"session_id": chat_session_id, "token": scripted, "done": True}) + "\n"

        mark_provider("scripted_sales")
        session.add(ChatMessage(session_id=chat_session_id, role="assistant", text=scripted, reason="scripted_sales"))
        session.commit()
        return StreamingResponse(scripted_gen(), media_type="application/x-ndjson")

    if not ok:
        def reject_gen():
            yield json.dumps({"session_id": chat_session_id, "token": reject, "done": True}) + "\n"

        session.add(ChatMessage(session_id=chat_session_id, role="assistant", text=reject, blocked=True, reason="policy"))
        session.commit()
        return StreamingResponse(reject_gen(), media_type="application/x-ndjson")

    recent = session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat_session_id).order_by(ChatMessage.created_at.desc())
    ).all()[:6]
    history = "\n".join([
        f"{'Клиент' if m.role == 'user' else 'Ассистент'}: {m.text}" for m in reversed(recent)
    ])

    prompt = f"{build_system_prompt(session)}\n\n{history}\nАссистент:"

    async def gen():
        assistant_full = ""
        try:
            async with _ollama_client.stream(
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
                        yield json.dumps({"session_id": chat_session_id, "token": token, "done": False}) + "\n"
                    if part.get("done"):
                        clean = sanitize_assistant_text(assistant_full)
                        with Session(engine) as write_session:
                            write_session.add(ChatMessage(session_id=chat_session_id, role="assistant", text=clean))
                            write_session.commit()
                        yield json.dumps({"session_id": chat_session_id, "token": "", "done": True}) + "\n"
                        break
        except Exception:
            try:
                llm_text, llm_provider = await generate_llm_response(prompt)
                clean_fallback_llm = sanitize_assistant_text(llm_text)
                mark_provider(llm_provider)
                with Session(engine) as write_session:
                    write_session.add(ChatMessage(session_id=chat_session_id, role="assistant", text=clean_fallback_llm, reason=llm_provider))
                    write_session.commit()
                yield json.dumps({"session_id": chat_session_id, "token": clean_fallback_llm, "done": True, "provider": llm_provider}) + "\n"
            except Exception:
                fallback = (
                    "Сервис генерации временно недоступен. "
                    "Уточните, пожалуйста, товар, класс и объем в тоннах — передам заявку менеджеру."
                )
                mark_provider("fallback")
                with Session(engine) as write_session:
                    write_session.add(ChatMessage(session_id=chat_session_id, role="assistant", text=fallback, blocked=True, reason="llm_unavailable"))
                    write_session.commit()
                yield json.dumps({"session_id": chat_session_id, "token": fallback, "done": True}) + "\n"

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
        mark_provider("quick_reply")
        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=fast, reason="quick_reply"))
        session.commit()
        return {"session_id": chat_session.id, "text": fast, "done": True, "provider": "quick_reply"}

    scripted = scripted_sales_reply(session, chat_session.id, payload.text)
    if scripted:
        mark_provider("scripted_sales")
        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=scripted, reason="scripted_sales"))
        session.commit()
        return {"session_id": chat_session.id, "text": scripted, "done": True, "provider": "scripted_sales"}

    if not ok:
        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=reject, blocked=True, reason="policy"))
        session.commit()
        return {"session_id": chat_session.id, "text": reject, "done": True, "provider": "policy"}

    recent = session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat_session.id).order_by(ChatMessage.created_at.desc())
    ).all()[:6]
    history = "\n".join([
        f"{'Клиент' if m.role == 'user' else 'Ассистент'}: {m.text}" for m in reversed(recent)
    ])
    prompt = f"{build_system_prompt(session)}\n\n{history}\nАссистент:"

    try:
        llm_text, llm_provider = await generate_llm_response(prompt)
        text = sanitize_assistant_text(llm_text)
        if not text:
            text = "Уточните, пожалуйста, товар, класс и объем в тоннах — передам заявку менеджеру."
        mark_provider(llm_provider)
        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=text, reason=llm_provider))
        session.commit()
        return {"session_id": chat_session.id, "text": text, "done": True, "provider": llm_provider}
    except Exception:
        fallback = (
            "Сервис генерации временно недоступен. "
            "Уточните, пожалуйста, товар, класс и объем в тоннах — передам заявку менеджеру."
        )
        mark_provider("fallback")
        session.add(ChatMessage(session_id=chat_session.id, role="assistant", text=fallback, blocked=True, reason="llm_unavailable"))
        session.commit()
        return {"session_id": chat_session.id, "text": fallback, "done": True, "provider": "fallback"}


@app.post("/api/chat/dry-run")
async def chat_dry_run(payload: ChatDryRunIn, session: Session = Depends(get_session)):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    scripted = scripted_sales_reply(session, chat_session_id=0, text=text)
    if scripted:
        mark_provider("scripted_sales")
        return {"done": True, "provider": "scripted_sales", "text": scripted}

    prompt = f"{build_system_prompt(session)}\n\nКлиент: {text}\nАссистент:"
    try:
        llm_text, llm_provider = await generate_llm_response(prompt)
        clean = sanitize_assistant_text(llm_text)
        if not clean:
            clean = "Уточните, пожалуйста, товар, класс и объем в тоннах — передам заявку менеджеру."
        mark_provider(llm_provider)
        return {"done": True, "provider": llm_provider, "text": clean}
    except Exception:
        mark_provider("fallback")
        return {
            "done": True,
            "provider": "fallback",
            "text": "Сервис генерации временно недоступен. Уточните, пожалуйста, товар, класс и объем в тоннах — передам заявку менеджеру.",
        }


@app.post("/api/picoclaw/agent/chat")
async def picoclaw_agent_chat(payload: PicoclawAgentIn, session: Session = Depends(get_session)):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    prompt = (
        f"{build_system_prompt(session)}\n\n"
        f"Контекст Picoclaw: {payload.context.strip()}\n"
        f"Запрос агента: {text}\n"
        f"Дай короткий ответ менеджера по продажам без выдумок."
    )
    llm_text, llm_provider = await generate_llm_response(prompt)
    clean = sanitize_assistant_text(llm_text)
    mark_provider(llm_provider)
    return {"done": True, "provider": llm_provider, "text": clean}


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
        "llm_usage": _llm_usage_stats,
        "last_provider": _last_provider,
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

