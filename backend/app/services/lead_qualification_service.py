from __future__ import annotations

from typing import Any

from ..domain.sales import REQUIRED_FIELDS, request_type_label


FIELD_LABELS = {
    "request_type": "тип заявки",
    "product": "культура",
    "volume": "объем",
    "region": "регион/маршрут",
    "timing": "срок",
    "contact": "контакт",
    "quality_class": "качество",
    "delivery_terms": "условия доставки",
    "company_name": "компания",
    "route_from": "маршрут откуда",
    "route_to": "маршрут куда",
    "transport_type": "транспорт",
    "comment": "комментарий",
}


class LeadQualificationService:
    def missing_fields(self, known_facts: dict[str, Any]) -> list[str]:
        return [field for field in REQUIRED_FIELDS if not self._has_value(known_facts.get(field))]

    def qualification_score(self, known_facts: dict[str, Any]) -> int:
        collected = len(REQUIRED_FIELDS) - len(self.missing_fields(known_facts))
        return int(round((collected / len(REQUIRED_FIELDS)) * 100))

    def captured_fields(self, known_facts: dict[str, Any]) -> list[str]:
        rows: list[str] = []
        for key in REQUIRED_FIELDS + ["quality_class", "delivery_terms", "company_name", "route_from", "route_to", "transport_type"]:
            value = known_facts.get(key)
            if not self._has_value(value):
                continue
            if key == "request_type":
                value = request_type_label(str(value))
            rows.append(f"{FIELD_LABELS.get(key, key)}: {value}")
        return rows

    def normalize_known_facts(self, facts: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in facts.items():
            if value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
            if self._has_value(value):
                cleaned[key] = value
        return cleaned

    @staticmethod
    def _has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return True
