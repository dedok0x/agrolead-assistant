from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from sqlmodel import Session, select

from ..domain.sales import DB_REQUEST_TYPE_BY_V7
from ..models import KnowledgeArticle, RefRequestType


@dataclass(slots=True)
class RetrievedContext:
    id: int
    title: str
    source_type: str
    content: str
    score: float
    has_price: bool = False
    has_availability: bool = False
    has_delivery_terms: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RAGService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def retrieve_context(
        self,
        query: str,
        intent: str | None,
        lead_state: dict[str, Any] | None,
        limit: int = 5,
    ) -> list[RetrievedContext]:
        rows = self.session.exec(select(KnowledgeArticle).where(KnowledgeArticle.is_active == True)).all()
        if not rows:
            return []

        request_type_id = self._request_type_id(intent)
        tokens = self._tokens(" ".join([query or "", " ".join(str(v) for v in (lead_state or {}).values())]))
        preferred_groups = self._preferred_groups(intent)
        scored: list[RetrievedContext] = []

        for row in rows:
            haystack = " ".join([row.title or "", row.short_answer or "", row.content_markdown or "", row.article_group or ""])
            score = 0.0
            lowered = haystack.lower()
            for token in tokens:
                if token in (row.title or "").lower():
                    score += 3
                if token in (row.short_answer or "").lower():
                    score += 2
                if token in lowered:
                    score += 1

            if row.article_group in preferred_groups:
                score += 2.5
            if request_type_id and row.request_type_id:
                score += 3 if row.request_type_id == request_type_id else -0.5
            elif row.request_type_id is None:
                score += 0.2

            if not tokens:
                score += max(0.1, (300 - row.sort_order) / 1000)
            if score <= 0:
                continue

            content = self._snippet(row.short_answer or row.content_markdown or "")
            scored.append(
                RetrievedContext(
                    id=row.id or 0,
                    title=row.title,
                    source_type=row.article_group,
                    content=content,
                    score=score,
                    has_price=self._has_price(content),
                    has_availability=self._has_availability(content),
                    has_delivery_terms=self._has_delivery_terms(content),
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(1, min(limit, 8))]

    def _request_type_id(self, intent: str | None) -> int | None:
        db_code = DB_REQUEST_TYPE_BY_V7.get(intent or "", intent or "")
        if not db_code:
            return None
        row = self.session.exec(select(RefRequestType).where(RefRequestType.code == db_code)).first()
        return row.id if row and row.id else None

    @staticmethod
    def _tokens(text: str) -> set[str]:
        normalized = (text or "").lower().replace("ё", "е")
        return {item for item in re.findall(r"[a-zа-я0-9]{3,}", normalized)}

    @staticmethod
    def _preferred_groups(intent: str | None) -> set[str]:
        # Базовый набор: инструкции квалификации, номенклатура и запрещённые
        # утверждения нужны почти всегда — на них опирается GigaChat при квалификации.
        base = {"qualification_script", "product_catalog", "forbidden_claim"}
        if intent in {"logistics", "ask_region", "clarify_delivery"}:
            return base | {"logistics_rule", "service_description", "faq"}
        if intent in {"storage"}:
            return base | {"storage_rule", "service_description", "faq"}
        if intent in {"export"}:
            return base | {"export_rule", "service_description", "faq"}
        if intent in {"handle_objection"}:
            return base | {"objection", "sales_script"}
        if intent in {"sell_grain", "buy_grain", "availability"}:
            return base | {"sales_script", "faq"}
        return base | {"company_profile", "service_description", "faq", "sales_script"}

    @staticmethod
    def _snippet(value: str, max_len: int = 420) -> str:
        cleaned = re.sub(r"`{1,3}", "", value or "")
        cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) <= max_len:
            return cleaned
        return cleaned[: max_len - 1].rstrip() + "..."

    @staticmethod
    def _has_price(text: str) -> bool:
        lowered = (text or "").lower()
        return any(marker in lowered for marker in ["руб", "₽", "цена", "стоимость", "прайс"])

    @staticmethod
    def _has_availability(text: str) -> bool:
        lowered = (text or "").lower()
        return any(marker in lowered for marker in ["налич", "доступн", "остаток"])

    @staticmethod
    def _has_delivery_terms(text: str) -> bool:
        lowered = (text or "").lower()
        return any(marker in lowered for marker in ["доставка", "срок", "маршрут", "fca", "cpt", "fob", "cfr", "exw"])
