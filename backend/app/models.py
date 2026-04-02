from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class CompanyProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    address: str
    phones: str
    email: str
    services: str
    contacts_markdown: str


class PromptCategory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    title: str
    content: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Scenario(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    description: str
    active: bool = True


class ScenarioTemplate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    goal: str
    starter_message: str
    active: bool = True


class ChatSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True)
    last_state: str = Field(default="greeting", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    role: str = Field(index=True)
    text: str
    blocked: bool = False
    reason: str = ""
    provider: str = ""
    model_used: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class ConversationState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True, unique=True)

    # greeting -> qualification -> offer -> handoff | stopped_toxic
    state: str = Field(default="greeting", index=True)
    last_question: str = ""
    missing_fields: str = ""
    toxicity_level: int = 0

    # Captured lead data
    product: str = ""
    grade: str = ""
    volume_tons: str = ""
    region: str = ""
    delivery_term: str = ""
    contact: str = ""

    # Diagnostics
    last_provider: str = ""
    last_model: str = ""
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ProductItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    culture: str = Field(index=True)
    grade: str = ""
    price_from: float = 0
    price_to: float = 0
    stock_tons: float = 0
    quality: str = ""
    location: str = ""
    active: bool = True
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Lead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[int] = Field(default=None, index=True)
    client_name: str = ""
    phone: str = ""
    email: str = ""
    product: str = ""
    grade: str = ""
    volume_tons: str = ""
    region: str = ""
    delivery_term: str = ""
    status: str = Field(default="in_progress", index=True)
    source: str = "chat"
    source_channel: str = Field(default="web", index=True)
    raw_dialogue: str = ""
    comment: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
