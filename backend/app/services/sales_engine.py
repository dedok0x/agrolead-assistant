from __future__ import annotations

import re
from typing import Any

from ..domain.sales import NextAction, RequestType, SalesDecision, SalesStage
from .lead_qualification_service import LeadQualificationService


PRODUCT_ALIASES = {
    "пшениц": "пшеница",
    "пшено": "пшено",
    "просо": "просо",
    "ячмен": "ячмень",
    "кукуруз": "кукуруза",
    "подсолнеч": "подсолнечник",
    "семечк": "подсолнечник",
    "соя": "соя",
    "рапс": "рапс",
    "горох": "горох",
    "зерно": "зерно",
}


REGION_ALIASES = {
    "краснодарский край": "Краснодарский край",
    "краснодар": "Краснодарский край",
    "кубан": "Краснодарский край",
    "ростовская область": "Ростовская область",
    "ростов": "Ростовская область",
    "ставропольский край": "Ставропольский край",
    "ставрополь": "Ставропольский край",
    "новороссийск": "Новороссийск",
    "порт": "порт",
    "тамань": "Тамань",
    "азов": "Азов",
    "ейск": "Ейск",
    "волгоград": "Волгоградская область",
    "самара": "Самарская область",
}


TRANSPORT_ALIASES = {
    "авто": "авто",
    "машин": "авто",
    "фур": "авто",
    "ж/д": "ж/д",
    "жд": "ж/д",
    "вагон": "ж/д",
    "вод": "водный транспорт",
    "суд": "водный транспорт",
}


FAQ_MARKERS = [
    "кто вы",
    "чем занимает",
    "услуги",
    "контакты",
    "где находитесь",
    "реквизиты",
    "как работает",
]


OBJECTION_MARKERS = [
    "дорого",
    "цена",
    "стоимость",
    "прайс",
    "сначала скажите",
    "условия",
    "наличие",
]


IRRELEVANT_MARKERS = [
    "погода",
    "анекдот",
    "рецепт",
    "игра",
    "музыка",
    "фильм",
    "политика",
]


class SalesEngine:
    def __init__(self, qualifier: LeadQualificationService | None = None) -> None:
        self.qualifier = qualifier or LeadQualificationService()

    def handle(self, text: str, known_facts: dict[str, Any] | None = None) -> SalesDecision:
        known = self.qualifier.normalize_known_facts(known_facts or {})
        extracted = self.extract(text, known)
        merged = {**known, **extracted}
        merged = self.qualifier.normalize_known_facts(merged)

        is_faq = self._is_faq(text)
        is_objection = self._is_objection(text)
        if self._is_irrelevant(text, merged):
            return SalesDecision(
                known_facts=merged,
                captured_fields=self.qualifier.captured_fields(merged),
                missing_fields=self.qualifier.missing_fields(merged),
                qualification_score=self.qualifier.qualification_score(merged),
                stage=SalesStage.IRRELEVANT,
                next_action=NextAction.REFUSE_IRRELEVANT,
                request_type=merged.get("request_type"),
                intent="irrelevant",
                extracted_fields=list(extracted),
            )

        missing = self.qualifier.missing_fields(merged)
        score = self.qualifier.qualification_score(merged)
        next_action = self._next_action(missing, is_faq=is_faq, is_objection=is_objection)
        stage = self._stage_for(score, next_action, bool(merged.get("request_type")))

        return SalesDecision(
            known_facts=merged,
            captured_fields=self.qualifier.captured_fields(merged),
            missing_fields=missing,
            qualification_score=score,
            stage=stage,
            next_action=next_action,
            request_type=merged.get("request_type"),
            intent=merged.get("request_type") or next_action.value,
            extracted_fields=list(extracted),
            is_faq=is_faq,
            is_objection=is_objection,
        )

    def extract(self, text: str, known_facts: dict[str, Any] | None = None) -> dict[str, Any]:
        known = known_facts or {}
        normalized = self._normalize(text)
        facts: dict[str, Any] = {}

        request_type = self._extract_request_type(normalized)
        if request_type:
            facts["request_type"] = request_type

        product = self._extract_product(normalized)
        if product:
            facts["product"] = product

        volume = self._extract_volume(text)
        if volume:
            facts["volume"] = volume

        contact = self._extract_contact(text)
        if contact:
            facts["contact"] = contact

        route_from, route_to = self._extract_route(normalized)
        if route_from:
            facts["route_from"] = route_from
        if route_to:
            facts["route_to"] = route_to
        region = self._extract_region(normalized, has_product=bool(product or known.get("product")))
        if route_from and route_to:
            facts["region"] = f"{route_from} -> {route_to}"
        elif region:
            facts["region"] = region

        timing = self._extract_timing(normalized)
        if timing:
            facts["timing"] = timing

        quality = self._extract_quality(normalized)
        if quality:
            facts["quality_class"] = quality

        delivery = self._extract_delivery_terms(normalized)
        if delivery:
            facts["delivery_terms"] = delivery

        company = self._extract_company(text)
        if company:
            facts["company_name"] = company

        transport = self._extract_transport(normalized)
        if transport:
            facts["transport_type"] = transport

        if normalized and normalized != (facts.get("comment") or ""):
            if self._looks_like_comment(normalized, facts):
                facts["comment"] = text.strip()[:500]

        return facts

    def _next_action(self, missing: list[str], *, is_faq: bool, is_objection: bool) -> NextAction:
        if is_objection:
            return NextAction.HANDLE_OBJECTION
        if is_faq and missing:
            return NextAction.ANSWER_FAQ
        if not missing:
            return NextAction.HANDOFF_MANAGER
        field = missing[0]
        return {
            "request_type": NextAction.ASK_REQUEST_TYPE,
            "product": NextAction.ASK_PRODUCT,
            "volume": NextAction.ASK_VOLUME,
            "region": NextAction.ASK_REGION,
            "timing": NextAction.ASK_TIMING,
            "contact": NextAction.ASK_CONTACT,
        }.get(field, NextAction.ASK_REQUEST_TYPE)

    @staticmethod
    def _stage_for(score: int, next_action: NextAction, has_request_type: bool) -> SalesStage:
        if next_action == NextAction.HANDOFF_MANAGER or score >= 100:
            return SalesStage.READY_FOR_MANAGER
        if not has_request_type:
            return SalesStage.NEW
        if score >= 75:
            return SalesStage.NEEDS_DISCOVERY
        return SalesStage.QUALIFICATION

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))

    @staticmethod
    def _extract_contact(text: str) -> str:
        phone = re.search(r"(?:\+7|8)[\d\s\-()]{9,}", text)
        if phone:
            raw = re.sub(r"\s+", " ", phone.group(0)).strip()
            return raw
        email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        if email:
            return email.group(0).strip()
        tg = re.search(r"(?<!\w)@([A-Za-z0-9_]{5,32})", text)
        if tg:
            return tg.group(0).strip()
        return ""

    @staticmethod
    def _extract_volume(text: str) -> str:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(тонн(?:а|ы)?|тн|т|кг)\b", text.lower())
        if not m:
            return ""
        value = m.group(1).replace(",", ".")
        unit = m.group(2)
        if unit in {"т", "тн", "тонн", "тонна", "тонны"}:
            unit = "тонн"
        return f"{value.rstrip('0').rstrip('.') if '.' in value else value} {unit}"

    @staticmethod
    def _extract_request_type(normalized: str) -> str:
        if any(word in normalized for word in ["логист", "перевоз", "доставка", "маршрут", "вагон", "фура"]):
            return RequestType.LOGISTICS.value
        if any(word in normalized for word in ["хранен", "элеватор", "склад", "перевалк"]):
            return RequestType.STORAGE.value
        if any(word in normalized for word in ["экспорт", "вэд", "fob", "cfr", "инкотермс"]):
            return RequestType.EXPORT.value
        if "налич" in normalized:
            return RequestType.AVAILABILITY.value
        if any(word in normalized for word in ["консультац", "проконсульт", "совет"]):
            return RequestType.CONSULTATION.value
        if any(word in normalized for word in ["продажа", "продать", "реализовать", "сдам", "предлагаю", "поставщик"]):
            return RequestType.SELL_GRAIN.value
        if any(word in normalized for word in ["купить", "покупка", "закупить", "нужна", "нужно", "приобрести", "интересует"]):
            return RequestType.BUY_GRAIN.value
        return ""

    @staticmethod
    def _extract_product(normalized: str) -> str:
        for marker, product in PRODUCT_ALIASES.items():
            if marker in normalized:
                return product
        return ""

    @staticmethod
    def _extract_region(normalized: str, *, has_product: bool) -> str:
        for marker in PRODUCT_ALIASES:
            if normalized.strip() == marker or PRODUCT_ALIASES[marker] == normalized.strip():
                return ""
        for marker, region in REGION_ALIASES.items():
            if marker in normalized:
                return region
        # Avoid treating an arbitrary single word as a region. This is what
        # previously caused "пшено" to leak into the delivery city field.
        if has_product:
            return ""
        return ""

    @staticmethod
    def _extract_route(normalized: str) -> tuple[str, str]:
        m = re.search(r"\bиз\s+([а-яa-z0-9 .-]{2,50})\s+в\s+([а-яa-z0-9 .-]{2,50})", normalized)
        if not m:
            return "", ""
        left = m.group(1).strip(" ,.")
        right = m.group(2).strip(" ,.")
        for stop in ["контакт", "тел", "телефон", "объем", "обьем"]:
            right = right.split(stop)[0].strip(" ,.")
        return left, right

    @staticmethod
    def _extract_timing(normalized: str) -> str:
        if any(word in normalized for word in ["срочно", "сегодня", "завтра", "сейчас"]):
            return "срочно"
        m = re.search(r"(?:до|к|на|в течение)\s+([а-я0-9 .-]{3,40})", normalized)
        if m and not any(skip in m.group(1) for skip in ["культура", "объем", "контакт"]):
            return m.group(0).strip(" ,.")
        if any(word in normalized for word in ["недел", "месяц", "май", "июн", "июл", "август", "сентябр", "квартал"]):
            return normalized[:80]
        date = re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", normalized)
        return date.group(0) if date else ""

    @staticmethod
    def _extract_quality(normalized: str) -> str:
        m = re.search(r"\b([1-6])\s*(?:класс|кл)\b", normalized)
        if m:
            return f"{m.group(1)} класс"
        if "фураж" in normalized:
            return "фураж"
        return ""

    @staticmethod
    def _extract_delivery_terms(normalized: str) -> str:
        for code in ["exw", "fca", "cpt", "daf", "fob", "cfr"]:
            if code in normalized:
                return code.upper()
        if "самовывоз" in normalized:
            return "самовывоз"
        if "доставка" in normalized:
            return "с доставкой"
        return ""

    @staticmethod
    def _extract_company(text: str) -> str:
        m = re.search(r"\b(ООО|АО|ПАО|ЗАО|ОАО|ИП|КФХ)\s+[A-Za-zА-Яа-я0-9\"«» .-]{2,80}", text)
        if not m:
            return ""
        return re.sub(r"\s+", " ", m.group(0)).strip(" ,.")

    @staticmethod
    def _extract_transport(normalized: str) -> str:
        for marker, value in TRANSPORT_ALIASES.items():
            if marker in normalized:
                return value
        return ""

    @staticmethod
    def _is_faq(text: str) -> bool:
        normalized = SalesEngine._normalize(text)
        return "?" in text and any(marker in normalized for marker in FAQ_MARKERS)

    @staticmethod
    def _is_objection(text: str) -> bool:
        normalized = SalesEngine._normalize(text)
        return any(marker in normalized for marker in OBJECTION_MARKERS)

    @staticmethod
    def _is_irrelevant(text: str, facts: dict[str, Any]) -> bool:
        normalized = SalesEngine._normalize(text)
        if facts.get("request_type") or facts.get("product") or facts.get("volume") or facts.get("contact"):
            return False
        return any(marker in normalized for marker in IRRELEVANT_MARKERS)

    @staticmethod
    def _looks_like_comment(normalized: str, facts: dict[str, Any]) -> bool:
        if len(normalized) < 12:
            return False
        meaningful = {"request_type", "product", "volume", "region", "timing", "contact"}
        return bool(meaningful.intersection(facts))
