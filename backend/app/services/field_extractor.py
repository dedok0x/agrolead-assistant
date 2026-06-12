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
    "Краснодарский край": ["краснодарский край", "краснодарском крае", "краснодар", "кубань"],
    "Ростовская область": ["ростовская область", "ростовской области", "ростов"],
    "Ставропольский край": ["ставропольский край", "ставрополь"],
    "Волгоградская область": ["волгоградская область", "волгоград"],
    "Воронежская область": ["воронежская область", "воронеж"],
    "Тамань": ["тамань"],
    "Азов": ["азов"],
    "Ейск": ["ейск"],
}

SELL_MARKERS = ["продаж", "продать", "продам", "продаем", "реализовать", "реализуем", "сдам", "сдадим", "поставщик", "есть на продажу"]
BUY_MARKERS = ["купить", "куплю", "покупк", "закуп", "приобрест", "приобрет", "возьмем", "ищем поставщика", "ищу поставщика"]
LOGISTICS_MARKERS = ["логист", "перевоз", "перевезти", "перевезем", "доставка", "доставить", "маршрут", "вагон", "фура", "автотранспорт"]
STORAGE_MARKERS = ["хранен", "хранить", "элеватор", "склад", "перевалк"]
EXPORT_MARKERS = ["экспорт", "вэд", "fob", "cfr"]
WEAK_BUY_MARKERS = ["нужна", "нужно", "нужен", "требуется", "интересует закупка"]

TIMING_PHRASES = [
    r"(?:на|до|к|в течение|в теч\.?)\s+(?:след(?:ующ\w*)?\s+)?(?:недел\w*|месяц\w*|квартал\w*|год\w*)",
    r"срок\w*\s+(?:до\s+)?[а-я0-9 .-]{2,30}?(?:месяц\w*|недел\w*|дн\w*|год\w*)",
    r"(?:два|три|четыре|пять|\d{1,2})\s+(?:месяц\w*|недел\w*|дн(?:я|ей))",
    r"(?:в|на|до|к)\s+(?:янв\w*|феврал\w*|март\w*|апрел\w*|ма[ея]|мае|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)",
    r"(?:следующ\w+|этой|будущ\w+)\s+недел\w*",
    r"(?:следующ\w+|этом|будущ\w+)\s+месяц\w*",
]


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
        rejected = self._rejected_fact(normalized)
        if rejected:
            return [rejected]
        fact_only_signals = {
            UserSignal.PROVIDES_FACT.value,
            UserSignal.OBJECTION_PRICE.value,
            UserSignal.WANTS_HUMAN.value,
        }
        if signal not in fact_only_signals:
            if signal == UserSignal.CONSULTATION_REQUEST.value:
                request_type = self._request_type(normalized)
                return [ExtractedFact("request_type", request_type, request_type, 0.95, text)] if request_type else []
            return []

        request_type = self._request_type(normalized, known_request_type=str(known.get("request_type") or ""))
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

        for route_fact in self._route(normalized):
            facts.append(route_fact)
        if not any(fact.field == "region" for fact in facts):
            region = self._region(normalized)
            if region and not self._is_only_product(normalized):
                facts.append(region)

        quality = self._quality(normalized, text)
        if quality:
            facts.append(quality)

        delivery = self._delivery(normalized)
        if delivery:
            facts.append(delivery)

        company = self._company(text)
        if company:
            facts.append(company)

        if signal in {UserSignal.VAGUE_ANSWER.value, UserSignal.REFUSAL_OR_UNKNOWN.value}:
            return [fact for fact in facts if fact.field not in {"volume", "timing"}]

        return facts

    @staticmethod
    def _request_type(text: str, known_request_type: str = "") -> str:
        has = lambda markers: any(marker in text for marker in markers)
        sell = has(SELL_MARKERS)
        buy = has(BUY_MARKERS)
        logistics = has(LOGISTICS_MARKERS)
        storage = has(STORAGE_MARKERS)
        export = has(EXPORT_MARKERS)

        # Хранение и экспорт — узкие маркеры, проверяем первыми.
        if storage and not (sell or buy):
            return RequestType.STORAGE.value
        if export and not (sell or buy):
            return RequestType.EXPORT.value
        # Явный глагол сделки сильнее, чем «доставка/перевозка» в той же фразе:
        # «купить 100 тонн с доставкой в Краснодар» — это покупка, а не логистика.
        if sell and not buy:
            return RequestType.SELL_GRAIN.value
        if buy and not sell:
            return RequestType.BUY_GRAIN.value
        if logistics:
            return RequestType.LOGISTICS.value
        if "налич" in text:
            return RequestType.AVAILABILITY.value
        if has(["консультац", "проконсульт"]):
            return RequestType.CONSULTATION.value
        # Слабые маркеры («нужна», «требуется») трактуем как покупку только когда
        # тип ещё не известен — иначе «нужно уточнить» перетирало бы продажу.
        if not known_request_type and has(WEAK_BUY_MARKERS):
            return RequestType.BUY_GRAIN.value
        return ""

    @staticmethod
    def _rejected_fact(text: str) -> ExtractedFact | None:
        if any(marker in text for marker in ["не просо", "не про просо", "да не просо", "нет не просо"]):
            return ExtractedFact("product", "", "", 1.0, text, status="rejected")
        if any(marker in text for marker in ["не пшено", "не про пшено", "да не пшено", "нет не пшено"]):
            return ExtractedFact("product", "", "", 1.0, text, status="rejected")
        return None

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
        m = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(тыс\.?\s*)?(тон\w*|тн|т|кг)\b", text)
        if m:
            value = m.group(1).replace(",", ".")
            if m.group(2):
                value = str(int(float(value) * 1000)) if float(value) == int(float(value)) else str(float(value) * 1000)
            unit = "кг" if m.group(3) == "кг" else "тонн"
            normalized_value = f"{value.rstrip('0').rstrip('.') if '.' in value else value} {unit}"
            return ExtractedFact("volume", normalized_value, normalized_value, 0.92, text)
        if current_next_action == NextAction.ASK_VOLUME.value:
            # Ответы на варианты, которые бот сам предлагает («до 100 тонн», «100–500»...)
            range_match = re.search(r"\b(\d{1,7})\s*[-–—]\s*(\d{1,7})\b", text)
            if range_match:
                value = f"{range_match.group(1)}-{range_match.group(2)} тонн"
                return ExtractedFact("volume", value, value, 0.9, text)
            bound = re.search(r"\b(до|больше|более|свыше|от)\s+(\d{1,7})\b", text)
            if bound:
                value = f"{bound.group(1)} {bound.group(2)} тонн"
                return ExtractedFact("volume", value, value, 0.9, text)
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
        for pattern in TIMING_PHRASES:
            phrase = re.search(pattern, text)
            if phrase:
                value = re.sub(r"\s+", " ", phrase.group(0)).strip()
                return ExtractedFact("timing", value, value, 0.88, text)
        # (?<![\d-])/(?![\d-]) — чтобы фрагменты телефона «111-22-33» не считались датой
        date = re.search(r"(?<![\d-])\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b(?![\d-])", text)
        if date:
            return ExtractedFact("timing", date.group(0), date.group(0), 0.9, text)
        if current_next_action == NextAction.ASK_TIMING.value and any(
            word in text
            for word in [
                "недел", "месяц", "квартал", "год", "сезон", "уборк", "осен", "весн", "зим", "лет",
                "январ", "феврал", "март", "апрел", "май", "мае", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр",
            ]
        ):
            value = text[:80].strip()
            return ExtractedFact("timing", value, value, 0.86, text)
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

    @classmethod
    def _route(cls, text: str) -> list[ExtractedFact]:
        route = re.search(r"\bиз\s+([а-яa-z0-9 .-]{2,40}?)\s+(?:в|до)\s+([а-яa-z0-9.-]+(?:\s+(?:край|область|обл\.?|порт))?)", text)
        if not route:
            return []
        left = cls._canonical_region(route.group(1).strip(" ,.")) or route.group(1).strip(" ,.")
        right = cls._canonical_region(route.group(2).strip(" ,.")) or route.group(2).strip(" ,.")
        value = f"{left} -> {right}"
        return [
            ExtractedFact("region", value, value, 0.88, text),
            ExtractedFact("route_from", left, left, 0.88, text),
            ExtractedFact("route_to", right, right, 0.88, text),
        ]

    @classmethod
    def _region(cls, text: str) -> ExtractedFact | None:
        for canonical, aliases in REGION_CANONICAL.items():
            for alias in aliases:
                if alias in text:
                    # Все алиасы словаря однозначны, поэтому форма слова («в Краснодаре»)
                    # не делает регион сомнительным фактом.
                    return ExtractedFact("region", canonical, canonical, 0.9 if alias == canonical.lower() else 0.88, text)
        return None

    @staticmethod
    def _canonical_region(value: str) -> str:
        lowered = value.lower()
        for canonical, aliases in REGION_CANONICAL.items():
            if any(alias in lowered for alias in aliases):
                return canonical
        return ""

    @staticmethod
    def _company(text: str) -> ExtractedFact | None:
        m = re.search(r"\b(ООО|АО|ПАО|ЗАО|ОАО|ИП|КФХ)\s+[«\"']?([А-ЯЁA-Z][\w «\"'-]{1,40})", text)
        if not m:
            return None
        name = re.split(r"[,.;]|\s(?:контакт|телефон|тел\b)", m.group(2))[0]
        value = f"{m.group(1)} {name.strip(' «»\"\'')}".strip()
        return ExtractedFact("company_name", value, value, 0.88, text)

    @staticmethod
    def _quality(text: str, source: str) -> ExtractedFact | None:
        m = re.search(r"\b([1-6])\s*-?\s*(?:го\s+)?(?:класс\w*|кл\b)", text)
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
