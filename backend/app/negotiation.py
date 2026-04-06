from typing import Optional


OBJECTION_MARKERS = [
    "дорого",
    "не подходит",
    "не устраивает",
    "слишком",
    "подумаем",
    "пока нет",
    "сомнева",
    "конкурент",
    "дешевле",
]


def looks_like_objection(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return any(marker in normalized for marker in OBJECTION_MARKERS)


def resolve_negotiation_stage(base_stage: str, status_code: str, user_text: str, missing_field: str) -> str:
    if status_code in {"qualified", "handed_to_manager"}:
        return "commitment_next_step"
    if looks_like_objection(user_text):
        return "objection_handling"
    if base_stage == "new":
        return "discovery"
    if base_stage == "faq":
        return "discovery"
    if base_stage == "partially_qualified":
        return "value_hypothesis"
    if not missing_field:
        return "proposal_draft"
    return "qualification"


def build_offer_hypothesis(
    request_type_code: str,
    fact_texts: dict[str, str],
    *,
    has_price_policy: bool,
    has_stock_hint: bool,
    missing_field: Optional[str],
) -> list[str]:
    lines: list[str] = []
    commodity = fact_texts.get("commodity_id") or "товар уточняется"
    volume = fact_texts.get("requested_volume_value") or fact_texts.get("volume_value") or "объем уточняется"

    if request_type_code == "purchase_from_supplier":
        lines.append(f"Можно рассмотреть оперативный выкуп партии: товар={commodity}, объем={volume}.")
    elif request_type_code == "sale_to_buyer":
        lines.append(f"Можно собрать предложение поставки: товар={commodity}, объем={volume}.")
    elif request_type_code == "logistics_request":
        route_from = fact_texts.get("route_from") or "точка отправки уточняется"
        route_to = fact_texts.get("route_to") or fact_texts.get("destination_region_id_or_port") or "точка назначения уточняется"
        lines.append(f"Можно подготовить логистический расчет по маршруту {route_from} -> {route_to}.")
    elif request_type_code == "export_request":
        lines.append("Можно подготовить экспортный контур с проверкой портовой логистики и базиса поставки.")
    else:
        lines.append("Можно собрать коммерческий контур после уточнения базовых параметров заявки.")

    if has_stock_hint:
        lines.append("Есть ориентир по доступности, можно обсудить подтверждение объема и сроков.")
    if has_price_policy:
        lines.append("Есть применимые ценовые правила, после уточнения условий можно перейти к коммерческому предложению.")
    if missing_field:
        lines.append(f"Критично уточнить: {missing_field}.")

    return lines[:3]
