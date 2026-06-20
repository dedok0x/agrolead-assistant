"""Гарантия, что в пользовательский чат не попадают служебные сообщения.

GigaChat за ход возвращает структурированный JSON, но наружу должна уходить
только человеческая реплика (`reply_text`). Этот модуль отбраковывает любой
ответ, в котором просочились JSON, имена служебных полей, коды действий или
markdown-разметка таблиц.
"""

from __future__ import annotations

import re

# Имена служебных полей и внутренних кодов, которых не должно быть в чате.
SERVICE_TOKENS = (
    "request_type",
    "updated_facts",
    "newly_extracted",
    "missing_fields",
    "qualification_status",
    "qualification_score",
    "is_service_message",
    "known_facts",
    "reply_text",
    "next_action",
    "source_channel",
    "retrieved_context",
    "forbidden_claims",
    "ask_request_type",
    "ask_product",
    "ask_volume",
    "ask_region",
    "ask_timing",
    "ask_contact",
    "answer_faq",
    "handoff_manager",
    "sale_to_buyer",
    "purchase_from_supplier",
    "logistics_request",
    "storage_request",
    "export_request",
    "general_company_request",
)


def _strip_code_fences(text: str) -> str:
    cleaned = re.sub(r"```[a-zA-Z]*", "", text)
    return cleaned.replace("```", "").strip()


def looks_like_service_text(text: str) -> bool:
    """True, если в тексте есть признаки служебного/системного контента."""
    if not text:
        return True
    lowered = text.lower()
    if "{" in text or "}" in text:
        return True
    if "|---" in text or re.search(r"\|\s*-{2,}\s*\|", text):
        return True
    return any(token in lowered for token in SERVICE_TOKENS)


def sanitize_reply(text: str) -> str | None:
    """Возвращает очищенную реплику или None, если текст служебный/пустой."""
    candidate = _strip_code_fences(text or "")
    candidate = re.sub(r"\s+\n", "\n", candidate).strip()
    if not candidate or looks_like_service_text(candidate):
        return None
    return candidate
