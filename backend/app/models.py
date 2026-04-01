from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field


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


class ChatSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    role: str
    text: str
    blocked: bool = False
    reason: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

