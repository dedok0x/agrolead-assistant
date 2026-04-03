import re
from dataclasses import dataclass

REQUEST_TYPE_KEYWORDS = {
    "purchase_from_supplier": ["продать", "сдам", "поставщик", "закупк", "купите у нас", "реализовать"],
    "sale_to_buyer": ["купить", "нужна", "нужно", "интересует", "покупка", "приобрести"],
    "logistics_request": ["логист", "перевоз", "машин", "вагон", "маршрут", "доставка"],
    "storage_request": ["хранени", "элеватор", "перевалк", "склад"],
    "export_request": ["экспорт", "fob", "cfr", "инкотермс", "вэд", "иностран"],
}


REQUIRED_FIELDS_BY_REQUEST = {
    "purchase_from_supplier": [
        "commodity_id",
        "volume_value",
        "volume_unit",
        "source_region_id",
        "contact_name_or_company",
        "contact_phone_or_telegram_or_email",
    ],
    "sale_to_buyer": [
        "commodity_id",
        "requested_volume_value",
        "requested_volume_unit",
        "destination_region_id_or_port",
        "contact_name_or_company",
        "contact_phone_or_telegram_or_email",
    ],
    "logistics_request": [
        "transport_mode_id",
        "route_from",
        "route_to",
        "volume_value",
        "cargo_type_text",
        "contact_name_or_company",
        "contact_phone_or_telegram_or_email",
    ],
    "storage_request": [
        "location_text",
        "volume_value",
        "inbound_mode",
        "outbound_mode_or_plan",
        "storage_period_text",
        "contact_name_or_company",
        "contact_phone_or_telegram_or_email",
    ],
    "export_request": [
        "commodity_id",
        "volume_value",
        "destination_country",
        "port_text",
        "contact_name_or_company",
        "contact_phone_or_telegram_or_email",
    ],
    "general_company_request": [
        "request_type_hint",
        "contact_name_or_company",
        "contact_phone_or_telegram_or_email",
    ],
}


FIELD_QUESTION = {
    "request_type_hint": "Уточните тип заявки: продажа, покупка, логистика, хранение, экспорт или просто консультация?",
    "commodity_id": "Какую культуру фиксируем в заявке?",
    "volume_value": "Какой ориентир по объему в тоннах?",
    "volume_unit": "Подтвердите единицу объема: тонны или другая?",
    "source_region_id": "Из какого региона планируется отгрузка?",
    "destination_region_id_or_port": "Куда нужна поставка: город или регион доставки?",
    "contact_name_or_company": "Как к вам обращаться: имя или название компании?",
    "contact_phone_or_telegram_or_email": "Оставьте удобный контакт: телефон, Telegram или email.",
    "transport_mode_id": "Какой вид транспорта нужен: авто, ж/д или вода?",
    "route_from": "Откуда стартует маршрут?",
    "route_to": "Куда доставляем груз?",
    "cargo_type_text": "Какой именно груз везем?",
    "location_text": "В какой локации требуется хранение/перевалка?",
    "inbound_mode": "Как груз приходит на площадку: авто, ж/д, вода?",
    "outbound_mode_or_plan": "Какой план по отгрузке с площадки?",
    "storage_period_text": "На какой срок требуется хранение?",
    "destination_country": "Какая страна назначения по экспорту?",
    "port_text": "Через какой порт планируете отгрузку?",
}


@dataclass(slots=True)
class FactValue:
    text: str = ""
    numeric: float | None = None
    confidence: float = 0.5


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def detect_request_type(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return "general_company_request"

    scores: dict[str, int] = {key: 0 for key in REQUEST_TYPE_KEYWORDS}
    for request_code, words in REQUEST_TYPE_KEYWORDS.items():
        for word in words:
            if word in normalized:
                scores[request_code] += 1

    has_route = bool(re.search(r"\bиз\s+[а-яa-z0-9\-\s]{2,60}\s+в\s+[а-яa-z0-9\-\s]{2,60}", normalized))
    has_transport_marker = any(
        marker in normalized
        for marker in ["авто", "машин", "фура", "вагон", "ж/д", "жд", "баржа", "судно", "логист", "перевоз", "доставка"]
    )

    # экспорт важнее простой логистики
    if scores["export_request"] > 0:
        return "export_request"

    # логистика должна перебивать общий "нужна/нужно" из покупки
    if scores["logistics_request"] > 0 and (has_route or has_transport_marker):
        return "logistics_request"
    if has_route and has_transport_marker:
        return "logistics_request"

    best = max(scores.items(), key=lambda item: item[1])
    if best[1] == 0:
        return "general_company_request"

    generic_buy_marker = any(word in normalized for word in ["нужна", "нужно", "интересует"])
    explicit_trade_marker = any(word in normalized for word in ["купить", "покупка", "продать", "продажа", "закупка", "поставщик"])
    if best[0] == "sale_to_buyer" and best[1] == 1 and generic_buy_marker and not explicit_trade_marker:
        return "general_company_request"

    if scores["purchase_from_supplier"] > 0 and scores["sale_to_buyer"] > 0 and abs(scores["purchase_from_supplier"] - scores["sale_to_buyer"]) <= 1:
        return "general_company_request"

    return best[0]


def parse_request_type_hint(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""

    if any(word in normalized for word in ["логист", "перевоз", "доставка", "маршрут", "вагон", "авто", "ж/д", "жд"]):
        return "logistics_request"
    if any(word in normalized for word in ["хранен", "склад", "элеватор", "перевалк"]):
        return "storage_request"
    if any(word in normalized for word in ["экспорт", "fob", "cfr", "вэд"]):
        return "export_request"
    if any(word in normalized for word in ["прода", "реализ", "поставщик", "закупите у нас"]):
        return "purchase_from_supplier"
    if any(word in normalized for word in ["купить", "покупка", "приобрести", "нужна закупка"]):
        return "sale_to_buyer"
    if any(word in normalized for word in ["кто вы", "какие услуги", "консультац", "информ", "узнать"]):
        return "general_company_request"
    return ""


def parse_contact(text: str) -> str:
    phone = re.search(r"(?:\+7|8)[\d\s\-\(\)]{9,}", text)
    if phone:
        return re.sub(r"\s+", " ", phone.group(0)).strip()

    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if email:
        return email.group(0).strip()

    tg = re.search(r"@([A-Za-z0-9_]{5,32})", text)
    if tg:
        return tg.group(0).strip()
    return ""


def parse_volume(text: str) -> tuple[float | None, str]:
    m = re.search(r"(\d+[\.,]?\d*)\s*(тонн|тонны|тонна|тн|т|кг)", normalize_text(text))
    if not m:
        return None, ""
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit in {"т", "тн", "тонн", "тонны", "тонна"}:
        return value, "тонна"
    return value, unit


def parse_route(text: str) -> tuple[str, str]:
    normalized = normalize_text(text)
    m = re.search(r"из\s+([а-яa-z\-\s]{2,60})\s+в\s+([а-яa-z\-\s]{2,60})", normalized)
    if not m:
        return "", ""
    return m.group(1).strip(" ,."), m.group(2).strip(" ,.")


def parse_destination_location(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""

    _, route_to = parse_route(text)
    if route_to:
        return route_to

    city_marker = re.search(r"(?:\bг\.?|\bгород)\s*([а-яa-z\-]{2,40})", normalized)
    if city_marker:
        return city_marker.group(1).strip(" ,.")

    preposition = re.search(r"(?:\bв|\bдо)\s+([а-яa-z\-]{3,40})", normalized)
    if preposition:
        token = preposition.group(1).strip(" ,.")
        stop = {
            "наличие",
            "продаже",
            "покупке",
            "логистике",
            "склад",
            "пшеницы",
            "ячменя",
            "кукурузы",
            "зерна",
            "тонн",
        }
        if token not in stop:
            return token

    chunks = [item.strip() for item in normalized.split(",") if item.strip()]
    if chunks:
        first = chunks[0]
        words = first.split()
        if 1 <= len(words) <= 3 and not any(ch.isdigit() for ch in first):
            black_list = {
                "купить",
                "продать",
                "логистика",
                "доставка",
                "хранение",
                "консультация",
                "пшеница",
                "ячмень",
                "кукуруза",
                "все",
                "всё",
            }
            if not any(word in black_list for word in words):
                return first

    return ""


def parse_contact_name_or_company(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""

    noise_tokens = [
        "нужн",
        "куп",
        "прод",
        "логист",
        "достав",
        "маршрут",
        "тонн",
        "цена",
        "зерн",
        "пшениц",
        "ячмен",
        "кукуруз",
        "контакт",
        "заявк",
    ]

    def _sanitize(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value.strip(" .,:;"))
        cleaned = cleaned.replace("«", '"').replace("»", '"')
        return cleaned

    def _is_noise(value: str) -> bool:
        low = value.lower()
        if len(low) < 2 or len(low) > 90:
            return True
        if "@" in low or re.search(r"(?:\+7|8)\d{9,}", low):
            return True
        if any(token in low for token in noise_tokens):
            return True
        return False

    company_match = re.search(
        r"\b((?:ооо|ао|пао|зао|оао|ип|кфх)\s+[а-яa-z0-9\-\"«»\s]{2,80})",
        normalized,
    )
    if company_match:
        candidate = _sanitize(company_match.group(1))
        if not _is_noise(candidate):
            return candidate

    company_word_match = re.search(r"(?:компания|организация)\s*[:\-]?\s*([а-яa-z0-9\-\"«»\s]{2,80})", normalized)
    if company_word_match:
        candidate = _sanitize(company_word_match.group(1))
        if not _is_noise(candidate):
            return candidate

    person_match = re.search(
        r"(?:меня зовут|контакт(?:ное лицо)?|обращаться к|на имя)\s*[:\-]?\s*([а-яa-z][а-яa-z\-]{1,30}(?:\s+[а-яa-z][а-яa-z\-]{1,30}){0,2})",
        normalized,
    )
    if person_match:
        candidate = _sanitize(person_match.group(1))
        if not _is_noise(candidate):
            return candidate

    return ""


def parse_quality_text(text: str) -> str:
    normalized = normalize_text(text)
    quality_tokens = ["класс", "протеин", "клейков", "влажност", "натура", "сорная примесь", "фураж"]
    if any(token in normalized for token in quality_tokens):
        return text.strip()
    return ""


def parse_transport_code(text: str) -> str:
    normalized = normalize_text(text)
    if any(word in normalized for word in ["жд", "ж/д", "вагон", "железнодорож"]):
        return "rail"
    if any(word in normalized for word in ["вода", "суд", "баржа"]):
        return "water"
    if any(word in normalized for word in ["авто", "машин", "фура", "truck"]):
        return "road"
    return ""


def parse_delivery_basis_code(text: str) -> str:
    normalized = normalize_text(text)
    for code in ["exw", "fca", "cpt", "daf", "cfr", "fob"]:
        if code in normalized:
            return code
    return ""


def parse_destination_country(text: str) -> str:
    normalized = normalize_text(text)
    countries = ["турц", "егип", "иран", "китай", "оаэ", "сауд"]
    for c in countries:
        if c in normalized:
            if c == "турц":
                return "Турция"
            if c == "егип":
                return "Египет"
            if c == "иран":
                return "Иран"
            if c == "китай":
                return "Китай"
            if c == "оаэ":
                return "ОАЭ"
            if c == "сауд":
                return "Саудовская Аравия"
    return ""


def parse_port_text(text: str) -> str:
    normalized = normalize_text(text)
    if "новороссий" in normalized:
        return "Новороссийск"
    if "таман" in normalized:
        return "Тамань"
    if "порт" in normalized:
        return text.strip()
    return ""


def parse_urgency(text: str) -> str:
    normalized = normalize_text(text)
    if any(word in normalized for word in ["срочно", "сегодня", "сейчас", "как можно быстрее"]):
        return "high"
    return ""


def extract_facts(
    text: str,
    commodity_by_name: dict[str, int],
    region_by_name: dict[str, int],
) -> dict[str, FactValue]:
    facts: dict[str, FactValue] = {}
    normalized = normalize_text(text)

    for name, commodity_id in commodity_by_name.items():
        if name in normalized:
            facts["commodity_id"] = FactValue(text=str(commodity_id), numeric=float(commodity_id), confidence=0.92)
            facts.setdefault("cargo_type_text", FactValue(text=name, confidence=0.70))
            break

    volume_value, volume_unit = parse_volume(text)
    if volume_value is not None:
        facts["volume_value"] = FactValue(text=str(volume_value), numeric=volume_value, confidence=0.95)
        facts["requested_volume_value"] = FactValue(text=str(volume_value), numeric=volume_value, confidence=0.95)
    if volume_unit:
        facts["volume_unit"] = FactValue(text=volume_unit, confidence=0.95)
        facts["requested_volume_unit"] = FactValue(text=volume_unit, confidence=0.95)

    contact = parse_contact(text)
    if contact:
        facts["contact_phone_or_telegram_or_email"] = FactValue(text=contact, confidence=0.98)

    request_type_hint = parse_request_type_hint(text)
    if request_type_hint:
        facts["request_type_hint"] = FactValue(text=request_type_hint, confidence=0.9)

    company_or_name = parse_contact_name_or_company(text)
    if company_or_name:
        facts["contact_name_or_company"] = FactValue(text=company_or_name, confidence=0.70)

    quality = parse_quality_text(text)
    if quality:
        facts["quality_profile_text"] = FactValue(text=quality, confidence=0.75)
        facts["requested_quality_text"] = FactValue(text=quality, confidence=0.75)

    transport_code = parse_transport_code(text)
    if transport_code:
        facts["transport_mode_code"] = FactValue(text=transport_code, confidence=0.9)
        facts["inbound_mode"] = FactValue(text=transport_code, confidence=0.75)
        facts["outbound_mode_or_plan"] = FactValue(text=transport_code, confidence=0.70)

    basis_code = parse_delivery_basis_code(text)
    if basis_code:
        facts["delivery_basis_code"] = FactValue(text=basis_code, confidence=0.88)

    route_from, route_to = parse_route(text)
    if route_from:
        facts["route_from"] = FactValue(text=route_from, confidence=0.85)
    if route_to:
        facts["route_to"] = FactValue(text=route_to, confidence=0.85)

    destination_location = parse_destination_location(text)
    if destination_location:
        facts.setdefault("destination_region_id_or_port", FactValue(text=destination_location, confidence=0.72))
        facts.setdefault("location_text", FactValue(text=destination_location, confidence=0.68))

    destination_country = parse_destination_country(text)
    if destination_country:
        facts["destination_country"] = FactValue(text=destination_country, confidence=0.9)

    port_text = parse_port_text(text)
    if port_text:
        facts["port_text"] = FactValue(text=port_text, confidence=0.9)

    urgency = parse_urgency(text)
    if urgency:
        facts["urgency"] = FactValue(text=urgency, confidence=0.8)

    for region_name, region_id in region_by_name.items():
        if region_name in normalized:
            facts.setdefault("source_region_id", FactValue(text=str(region_id), numeric=float(region_id), confidence=0.84))
            facts.setdefault(
                "destination_region_id_or_port",
                FactValue(text=str(region_id), numeric=float(region_id), confidence=0.8),
            )
            facts.setdefault("location_text", FactValue(text=region_name, confidence=0.7))

    if "хран" in normalized or "склад" in normalized or "перевалк" in normalized:
        facts.setdefault("storage_period_text", FactValue(text="срок уточняется", confidence=0.55))

    if "экспорт" in normalized:
        facts.setdefault("export_flag", FactValue(text="1", numeric=1, confidence=0.9))

    return facts


def required_fields(request_type_code: str) -> list[str]:
    return list(REQUIRED_FIELDS_BY_REQUEST.get(request_type_code, REQUIRED_FIELDS_BY_REQUEST["general_company_request"]))


def next_missing_field(required: list[str], collected: set[str]) -> str:
    for code in required:
        if code not in collected:
            return code
    return ""


def minimum_viable_application(request_type_code: str, fact_keys: set[str], has_contact: bool) -> bool:
    if not request_type_code:
        return False

    has_subject = any(key in fact_keys for key in ["commodity_id", "cargo_type_text", "location_text"])
    has_scope = any(key in fact_keys for key in ["volume_value", "requested_volume_value", "route_from", "source_region_id", "destination_region_id_or_port"])
    return has_subject and has_scope and has_contact


def human_field_name(field_code: str) -> str:
    names = {
        "commodity_id": "культура",
        "request_type_hint": "тип заявки",
        "volume_value": "объем",
        "volume_unit": "единица объема",
        "source_region_id": "регион отгрузки",
        "destination_region_id_or_port": "город/регион доставки",
        "contact_name_or_company": "компания/контакт",
        "contact_phone_or_telegram_or_email": "контакт для связи",
        "transport_mode_id": "вид транспорта",
        "route_from": "маршрут откуда",
        "route_to": "маршрут куда",
        "cargo_type_text": "тип груза",
        "location_text": "локация",
        "inbound_mode": "входящий транспорт",
        "outbound_mode_or_plan": "выходящий транспорт/план",
        "storage_period_text": "срок хранения",
        "destination_country": "страна назначения",
        "port_text": "порт",
    }
    return names.get(field_code, field_code)


def next_question_for(field_code: str) -> str:
    return FIELD_QUESTION.get(field_code, "Уточните следующий важный параметр по сделке.")
