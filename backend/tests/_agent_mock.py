"""Утилиты для тестов GigaChat-агента: подменяем сетевой вызов GigaChat
заранее заданными JSON-ответами, проходя при этом реальный парсинг/валидацию."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock


def turn_json(
    *,
    reply_text: str = "Принял, уточню детали и продолжим.",
    intent: str = "commercial",
    is_service_message: bool = False,
    request_type: str | None = None,
    updated_facts: dict[str, Any] | None = None,
    newly_extracted: list[str] | None = None,
    model: str = "GigaChat-2",
) -> tuple[str, str]:
    """Готовит (json_str, model) как будто это ответ GigaChat chat_completion."""
    payload = {
        "intent": intent,
        "is_service_message": is_service_message,
        "request_type": request_type,
        "updated_facts": updated_facts or {},
        "newly_extracted": newly_extracted or [],
        "reply_text": reply_text,
    }
    return json.dumps(payload, ensure_ascii=False), model


def install_gigachat(llm_service, responses: list[tuple[str, str]]) -> AsyncMock:
    """Включает мок: chat_completion последовательно возвращает responses."""
    llm_service.gigachat_client.auth_key = "test-key"
    mock = AsyncMock(side_effect=list(responses))
    llm_service.gigachat_client.chat_completion = mock
    return mock


def install_gigachat_raw(llm_service, raw_responses: list[str], model: str = "GigaChat-2") -> AsyncMock:
    """Как install_gigachat, но raw-строки (для проверки устойчивого парсинга/утечек)."""
    return install_gigachat(llm_service, [(raw, model) for raw in raw_responses])


def install_fake_gigachat(llm_service, model: str = "GigaChat-2") -> AsyncMock:
    """Детерминированный офлайн-двойник GigaChat для сквозных тестов.

    Имитирует структурированный ответ модели, переиспользуя существующий
    детерминированный экстрактор/квалификатор. В проде извлечение делает реальный
    GigaChat; здесь — стабильная подмена без сети, говорящая на том же JSON-контракте.
    """
    import json

    from app.domain.sales import NEXT_ACTION_LABELS, NextAction, UserSignal
    from app.llm_service import _parse_json_object
    from app.services.field_extractor import FieldExtractor
    from app.services.lead_qualification_service import LeadQualificationService
    from app.services.sales_engine import SalesEngine
    from app.services.user_signal_service import UserSignalService

    qualifier = LeadQualificationService()
    engine = SalesEngine(qualifier, FieldExtractor())
    signals = UserSignalService()

    SERVICE_SIGNALS = {
        UserSignal.SMALLTALK.value,
        UserSignal.IRRELEVANT.value,
        UserSignal.META_DIALOGUE.value,
        UserSignal.ASKS_IDENTITY.value,
        UserSignal.ASKS_CAPABILITIES.value,
        UserSignal.ASKS_CLARIFICATION.value,
        UserSignal.CONSULTATION_REQUEST.value,
        UserSignal.NEGATIVE_FEEDBACK.value,
        UserSignal.FRUSTRATION.value,
        UserSignal.FAQ_QUESTION.value,
    }

    def _reply(decision, is_service: bool) -> str:
        if is_service:
            return "Понял вас. Помогу по зерну, логистике, хранению или ВЭД — что интересует?"
        if not decision.missing_fields:
            return "Заявку собрал, передаю менеджеру — он свяжется с вами в ближайшее время."
        label = NEXT_ACTION_LABELS.get(decision.next_action.value, "уточнить детали")
        return f"Принял, фиксирую. Подскажите, чтобы продолжить: {label}."

    def _fake(system_prompt, user_prompt, temperature=0.2, max_tokens=700):
        marker = "Данные backend:"
        raw = user_prompt.split(marker, 1)[-1] if marker in user_prompt else user_prompt
        payload = _parse_json_object(raw)
        user_message = str(payload.get("user_message") or "")
        known = dict(payload.get("known_facts") or {})

        current = engine.handle("", known)
        signal = signals.classify(user_message, current.next_action.value)
        decision = engine.handle(
            user_message, known, current_next_action=current.next_action.value, user_signal=signal
        )
        is_service = signal in SERVICE_SIGNALS and not decision.extracted_fields

        response = {
            "intent": "smalltalk" if is_service else "commercial",
            "is_service_message": is_service,
            "request_type": decision.known_facts.get("request_type"),
            "updated_facts": {} if is_service else decision.known_facts,
            "newly_extracted": [] if is_service else list(decision.extracted_fields),
            "reply_text": _reply(decision, is_service),
        }
        return json.dumps(response, ensure_ascii=False), model

    llm_service.gigachat_client.auth_key = "fake-key"
    mock = AsyncMock(side_effect=_fake)
    llm_service.gigachat_client.chat_completion = mock
    return mock
