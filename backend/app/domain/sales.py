from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SalesStage(str, Enum):
    NEW = "new"
    QUALIFICATION = "qualification"
    NEEDS_DISCOVERY = "needs_discovery"
    READY_FOR_MANAGER = "ready_for_manager"
    HANDED_TO_MANAGER = "handed_to_manager"
    IRRELEVANT = "irrelevant"


class NextAction(str, Enum):
    ASK_REQUEST_TYPE = "ask_request_type"
    ASK_PRODUCT = "ask_product"
    ASK_VOLUME = "ask_volume"
    ASK_REGION = "ask_region"
    ASK_TIMING = "ask_timing"
    ASK_CONTACT = "ask_contact"
    ANSWER_FAQ = "answer_faq"
    HANDLE_OBJECTION = "handle_objection"
    HANDOFF_MANAGER = "handoff_manager"
    CLARIFY_QUALITY = "clarify_quality"
    CLARIFY_DELIVERY = "clarify_delivery"
    REFUSE_IRRELEVANT = "refuse_irrelevant"


class RequestType(str, Enum):
    SELL_GRAIN = "sell_grain"
    BUY_GRAIN = "buy_grain"
    LOGISTICS = "logistics"
    STORAGE = "storage"
    EXPORT = "export"
    CONSULTATION = "consultation"
    AVAILABILITY = "availability"


REQUIRED_FIELDS = ["request_type", "product", "volume", "region", "timing", "contact"]
OPTIONAL_FIELDS = [
    "quality_class",
    "delivery_terms",
    "company_name",
    "route_from",
    "route_to",
    "transport_type",
    "comment",
]


REQUEST_TYPE_LABELS = {
    RequestType.SELL_GRAIN.value: "продажа зерна",
    RequestType.BUY_GRAIN.value: "покупка зерна",
    RequestType.LOGISTICS.value: "логистика",
    RequestType.STORAGE.value: "хранение",
    RequestType.EXPORT.value: "ВЭД / экспорт",
    RequestType.CONSULTATION.value: "консультация",
    RequestType.AVAILABILITY.value: "наличие товара",
}


NEXT_ACTION_LABELS = {
    NextAction.ASK_REQUEST_TYPE.value: "уточнить тип заявки",
    NextAction.ASK_PRODUCT.value: "уточнить культуру или груз",
    NextAction.ASK_VOLUME.value: "уточнить объем",
    NextAction.ASK_REGION.value: "уточнить регион или маршрут",
    NextAction.ASK_TIMING.value: "уточнить сроки",
    NextAction.ASK_CONTACT.value: "получить контакт",
    NextAction.ANSWER_FAQ.value: "ответить на вопрос",
    NextAction.HANDLE_OBJECTION.value: "обработать возражение",
    NextAction.HANDOFF_MANAGER.value: "передать менеджеру",
    NextAction.CLARIFY_QUALITY.value: "уточнить качество",
    NextAction.CLARIFY_DELIVERY.value: "уточнить доставку",
    NextAction.REFUSE_IRRELEVANT.value: "вежливо отказать",
}


DB_REQUEST_TYPE_BY_V7 = {
    RequestType.SELL_GRAIN.value: "purchase_from_supplier",
    RequestType.BUY_GRAIN.value: "sale_to_buyer",
    RequestType.LOGISTICS.value: "logistics_request",
    RequestType.STORAGE.value: "storage_request",
    RequestType.EXPORT.value: "export_request",
    RequestType.CONSULTATION.value: "general_company_request",
    RequestType.AVAILABILITY.value: "sale_to_buyer",
}


V7_REQUEST_TYPE_BY_DB = {value: key for key, value in DB_REQUEST_TYPE_BY_V7.items()}


@dataclass(slots=True)
class SalesDecision:
    known_facts: dict[str, Any] = field(default_factory=dict)
    captured_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    qualification_score: int = 0
    stage: SalesStage = SalesStage.NEW
    next_action: NextAction = NextAction.ASK_REQUEST_TYPE
    request_type: str | None = None
    intent: str | None = None
    extracted_fields: list[str] = field(default_factory=list)
    is_faq: bool = False
    is_objection: bool = False


def request_type_label(value: str | None) -> str:
    return REQUEST_TYPE_LABELS.get(value or "", value or "")


def next_action_label(value: str | None) -> str:
    return NEXT_ACTION_LABELS.get(value or "", value or "")
