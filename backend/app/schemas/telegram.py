from __future__ import annotations

from pydantic import BaseModel, Field


class TelegramWebhookUpdate(BaseModel):
    update_id: int | None = None
    message: dict | None = None
    callback_query: dict | None = None


class TelegramWebhookResponse(BaseModel):
    ok: bool = True
    enabled: bool = False
    handled: bool = False
    detail: str = ""


class TelegramStatus(BaseModel):
    enabled: bool
    token_configured: bool
    webhook_url: str = ""
    last_error: str = ""
    raw: dict = Field(default_factory=dict)
