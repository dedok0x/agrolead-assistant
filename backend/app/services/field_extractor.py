from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from ..domain.sales import NextAction, RequestType, UserSignal


@dataclass(slots=True)
class ExtractedFact:
    field: str
    value: Any
    normalized_value: Any
    confidence: float
    source_text: str
    status: str = "confirmed"


PRODUCT_CANONICAL = {
    "пшеница": ["пшеница", "пшениц", "пшениица", "пшеницаа"],
    "ячмень": ["ячмень", "ячмен"],
    "кукуруза": ["кукуруза", "кукуруз"],
    "подсолнечник": ["подсолнечник", "подсолнеч", "семечка", "семечки"],
    "соя": ["соя"],
    "рапс": ["рапс"],
    "горох": ["горох"],
    "пшено": ["пшено"],
    "просо": ["просо"],
    "зерно": ["зерно"],
    "гречиха": ["гречиха", "греча", "гречка"],
}

REGION_CANONICAL = {
    "Новороссийск": ["новороссийск", "новорос"],
    "Краснодарский край": ["краснодарский край", "краснодар", "кубань"],
    "Ростовская область": ["ростовская область", "ростов"],
    "Ставропольский край": ["ставропольский край", "ставрополь"],
    "Тамань": ["тамань"],
    "Азов": ["азов"],
    "Ейск": ["ейск"],
}


class FieldExtractor:
    confirmed_threshold = 0.85

    def extract(
        self,
        user_message: str,
        *,
        current_next_action: str | None = None,
        known_facts: dict[str, Any] | None = None,
        user_signal: str | None = None,
    ) -> list[ExtractedFact]:
        text = user_message or ""
        normalized = normalize_text(text)
        facts: list[ExtractedFact] = []
        known = known_facts or {}
        signal = user_signal or UserSignal.PROVIDES_FACT.value

        request_type = self._request_type(normalized)
        if request_type:
            facts.append(ExtractedFact("request_type", request_type, request_type, 0.95, text))

        contact = self._contact(text)
        if contact:
            facts.append(ExtractedFact("contact", contact, contact, 0.92, text))

        volume = self._volume(normalized, current_next_action)
        if volume:
            facts.append(volume)

        timing = self._timing(normalized, current_next_action)
        if timing:
            facts.append(timing)

        product = self._product(normalized)
        if product:
            facts.append(product)

        region = self._region(normalized)
        if region and not self._is_only_product(normalized):
            facts.append(region)

        quality = self._quality(normalized, text)
        if quality:
            facts.append(quality)

        delivery = self._delivery(normalized)
        if delivery:
            facts.append(delivery)

        if signal in {UserSignal.VAGUE_ANSWER.value, UserSignal.REFUSAL_OR_UNKNOWN.value}:
            return [fact for fact in facts if fact.field not in {"volume", "timing"}]

        return facts

    @staticmethod
    def _request_type(text: str) -> str:
        if any(word in text for word in ["логист", "перевоз", "доставка", "маршрут", "вагон", "фура"]):
            return RequestType.LOGISTICS.value
        if any(word in text for word in ["хранен", "элеватор", "склад", "перевалк"]):
            return RequestType.STORAGE.value
        if any(word in text for word in ["экспорт", "вэд", "fob", "cfr"]):
            return RequestType.EXPORT.value
        if "налич" in text:
            return RequestType.AVAILABILITY.value
        if any(word in text for word in ["консультац", "проконсульт"]):
            return RequestType.CONSULTATION.value
        if any(word in text for word in ["продажа", "продать", "реализовать", "сдам", "поставщик"]):
            return RequestType.SELL_GRAIN.value
        if any(word in text for word in ["купить", "покупка", "закупить", "нужна", "нужно", "приобрести"]):
            return RequestType.BUY_GRAIN.value
        return ""

    @staticmethod
    def _contact(text: str) -> str:
        compact_digits = re.sub(r"\D", "", text or "")
        if len(compact_digits) == 11 and compact_digits.startswith("8"):
            return f"+7 {compact_digits[1:4]} {compact_digits[4:7]}-{compact_digits[7:9]}-{compact_digits[9:11]}"
        phone = re.search(r"(?:\+7|8)[\d\s\-()]{9,}", text)
        if phone:
            raw_digits = re.sub(r"\D", "", phone.group(0))
            if len(raw_digits) == 11 and raw_digits.startswith("8"):
                return f"+7 {raw_digits[1:4]} {raw_digits[4:7]}-{raw_digits[7:9]}-{raw_digits[9:11]}"
            return re.sub(r"\s+", " ", phone.group(0)).strip()
        email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        if email:
            return email.group(0).strip()
        tg = re.search(r"(?<!\w)@([A-Za-z0-9_]{5,32})", text)
        return tg.group(0).strip() if tg else ""

    @staticmethod
    def _volume(text: str, current_next_action: str | None) -> ExtractedFact | None:
        m = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(тон+|тн|т|кг)\b", text)
        if m:
            value = m.group(1).replace(",", ".")
            unit = "кг" if m.group(2) == "кг" else "тонн"
            normalized_value = f"{value.rstrip('0').rstrip('.') if '.' in value else value} {unit}"
            return ExtractedFact("volume", normalized_value, normalized_value, 0.92, text)
        if current_next_action == NextAction.ASK_VOLUME.value:
            numeric = re.fullmatch(r"\d{1,7}", text.strip())
            if numeric:
                value = f"{numeric.group(0)} тонн"
                return ExtractedFact("volume", value, value, 0.72, text, status="uncertain")
        return None

    @staticmethod
    def _timing(text: str, current_next_action: str | None) -> ExtractedFact | None:
        if any(marker in text for marker in ["как удобно", "по возможности", "не срочно", "без спешки", "когда удобно"]):
            return ExtractedFact("timing", "по согласованию", "по согласованию", 0.9, text)
        if any(word in text for word in ["срочно", "сегодня", "завтра", "сейчас"]):
            return ExtractedFact("timing", "срочно", "срочно", 0.9, text)
        if any(word in text for word in ["недел", "месяц", "май", "июн", "июл", "август", "сентябр", "квартал"]):
            return ExtractedFact("timing", text[:80], text[:80], 0.86, text)
        date = re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", text)
        if date:
            return ExtractedFact("timing", date.group(0), date.group(0), 0.9, text)
        return None

    @staticmethod
    def _product(text: str) -> ExtractedFact | None:
        for canonical, aliases in PRODUCT_CANONICAL.items():
            for alias in aliases:
                if alias in text:
                    confidence = 0.95 if alias == canonical or alias in {"пшениица", "пшеницаа"} else 0.88
                    status = "uncertain" if canonical == "гречиха" and alias in {"греча", "гречка"} else "confirmed"
                    confidence = 0.78 if status == "uncertain" else confidence
                    return ExtractedFact("product", canonical, canonical, confidence, text, status=status)
        words = [word for word in re.findall(r"[а-я]{4,}", text) if word not in {"продажа", "купить", "тонн", "большой"}]
        for word in words:
            best = ("", 0.0)
            for canonical in PRODUCT_CANONICAL:
                score = SequenceMatcher(None, word, canonical).ratio()
                if score > best[1]:
                    best = (canonical, score)
            if best[1] >= 0.74:
                status = "confirmed" if best[1] >= 0.84 else "uncertain"
                return ExtractedFact("product", best[0], best[0], best[1], text, status=status)
        return None

    @staticmethod
    def _region(text: str) -> ExtractedFact | None:
        route = re.search(r"\bиз\s+([а-яa-z0-9 .-]{2,50})\s+в\s+([а-яa-z0-9 .-]{2,50})", text)
        if route:
            left = route.group(1).strip(" ,.")
            right = route.group(2).strip(" ,.")
            return ExtractedFact("region", f"{left} -> {right}", f"{left} -> {right}", 0.86, text)
        for canonical, aliases in REGION_CANONICAL.items():
            for alias in aliases:
                if alias in text:
                    return ExtractedFact("region", canonical, canonical, 0.9 if alias == canonical.lower() else 0.82, text)
        return None

    @staticmethod
    def _quality(text: str, source: str) -> ExtractedFact | None:
        m = re.search(r"\b([1-6])\s*(?:класс|кл)\b", text)
        if m:
            value = f"{m.group(1)} класс"
            return ExtractedFact("quality_class", value, value, 0.9, source)
        if "фураж" in text:
            return ExtractedFact("quality_class", "фураж", "фураж", 0.86, source)
        return None

    @staticmethod
    def _delivery(text: str) -> ExtractedFact | None:
        for code in ["exw", "fca", "cpt", "daf", "fob", "cfr"]:
            if code in text:
                return ExtractedFact("delivery_terms", code.upper(), code.upper(), 0.9, text)
        if "самовывоз" in text:
            return ExtractedFact("delivery_terms", "самовывоз", "самовывоз", 0.86, text)
        return None

    @staticmethod
    def _is_only_product(text: str) -> bool:
        return any(text == alias or text == canonical for canonical, aliases in PRODUCT_CANONICAL.items() for alias in aliases)


def normalize_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))
    normalized = re.sub(r"\bтон{2,}\b", "тонн", normalized)
    return normalized
