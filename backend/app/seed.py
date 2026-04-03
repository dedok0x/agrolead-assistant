import hashlib
import os
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .models import (
    AdminSetting,
    AdminUser,
    CatalogPricePolicy,
    CatalogQualityTemplate,
    CatalogQualityTemplateLine,
    CatalogStockPlaceholder,
    CompanyProfile,
    KnowledgeArticle,
    RefCommodity,
    RefCounterpartyType,
    RefDeliveryBasis,
    RefDepartment,
    RefLeadSource,
    RefManagerRole,
    RefPipelineStage,
    RefQualityParameter,
    RefRegion,
    RefRequestType,
    RefTransportMode,
)


def _hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _upsert_by_code(session: Session, model, code: str, payload: dict) -> None:
    row = session.exec(select(model).where(model.code == code)).first()
    if row:
        for key, value in payload.items():
            setattr(row, key, value)
        session.add(row)
        return
    session.add(model(**payload))


def _ensure_reference_catalogs(session: Session) -> None:
    commodities = [
        ("wheat", "Пшеница", "Пшеница продовольственная и фуражная", "grain", 10),
        ("barley", "Ячмень", "Ячмень кормовой и пивоваренный", "grain", 20),
        ("corn", "Кукуруза", "Кукуруза зерновая", "grain", 30),
        ("sunflower", "Подсолнечник", "Подсолнечник масличный", "oilseed", 40),
        ("peas", "Горох", "Горох продовольственный", "pulse", 50),
        ("chickpeas", "Нут", "Нут продовольственный", "pulse", 60),
        ("flax", "Лен", "Лен масличный", "oilseed", 70),
        ("coriander", "Кориандр", "Кориандр продовольственный", "spice", 80),
    ]
    for code, name, full_name, group, sort_order in commodities:
        _upsert_by_code(
            session,
            RefCommodity,
            code,
            {
                "code": code,
                "name": name,
                "full_name": full_name,
                "commodity_group": group,
                "unit_of_measure_default": "тонна",
                "is_active": True,
                "sort_order": sort_order,
                "updated_at": datetime.utcnow(),
            },
        )

    quality_parameters = [
        ("protein", "Протеин", "number", "%", 10),
        ("moisture", "Влажность", "number", "%", 20),
        ("gluten", "Клейковина", "number", "%", 30),
        ("test_weight", "Натура", "number", "г/л", 40),
        ("foreign_matter", "Сорная примесь", "number", "%", 50),
    ]
    for code, name, value_type, unit, sort_order in quality_parameters:
        _upsert_by_code(
            session,
            RefQualityParameter,
            code,
            {
                "code": code,
                "name": name,
                "value_type": value_type,
                "unit": unit,
                "is_active": True,
                "sort_order": sort_order,
            },
        )

    delivery_basis = [
        ("exw", "EXW", "Самовывоз со склада продавца"),
        ("fca", "FCA", "Погрузка на станции/терминале отправления"),
        ("cpt", "CPT", "Доставка до согласованного пункта"),
        ("daf", "DAF", "Поставка до границы"),
        ("cfr", "CFR", "Морской фрахт до порта назначения"),
    ]
    for idx, (code, name, description) in enumerate(delivery_basis, start=1):
        _upsert_by_code(
            session,
            RefDeliveryBasis,
            code,
            {
                "code": code,
                "name": name,
                "description": description,
                "is_active": True,
            },
        )

    transport_modes = [
        ("road", "Автотранспорт", "Внутрироссийская и экспортная авто-логистика"),
        ("rail", "Ж/д", "Железнодорожные отправки"),
        ("water", "Водный транспорт", "Речные и морские перевозки"),
        ("multimodal", "Мультимодально", "Комбинированные плечи"),
    ]
    for code, name, description in transport_modes:
        _upsert_by_code(
            session,
            RefTransportMode,
            code,
            {
                "code": code,
                "name": name,
                "description": description,
                "is_active": True,
            },
        )

    regions = [
        ("ru-kk", "Россия", "ЮФО", "Краснодарский край", "Краснодар", "", True),
        ("ru-ro", "Россия", "ЮФО", "Ростовская область", "Ростов-на-Дону", "", True),
        ("ru-st", "Россия", "СКФО", "Ставропольский край", "Ставрополь", "", True),
        ("ru-nov-port", "Россия", "ЮФО", "Краснодарский край", "Новороссийск", "Новороссийск", True),
        ("ru-tmn-port", "Россия", "ЮФО", "Краснодарский край", "Тамань", "Тамань", True),
        ("tr-mersin", "Турция", "", "Мерсин", "Мерсин", "Мерсин", True),
    ]
    for code, country, district, region_name, city_name, port_name, is_active in regions:
        _upsert_by_code(
            session,
            RefRegion,
            code,
            {
                "code": code,
                "country": country,
                "federal_district": district,
                "region_name": region_name,
                "city_name": city_name,
                "port_name": port_name,
                "is_active": is_active,
            },
        )

    lead_sources = [
        ("web_widget", "Виджет сайта", "web"),
        ("telegram", "Telegram", "messenger"),
        ("phone", "Телефон", "offline"),
        ("crm_import", "Импорт CRM", "integration"),
    ]
    for code, name, channel_type in lead_sources:
        _upsert_by_code(
            session,
            RefLeadSource,
            code,
            {
                "code": code,
                "name": name,
                "channel_type": channel_type,
                "is_active": True,
            },
        )

    counterparty_types = [
        ("supplier", "Поставщик"),
        ("buyer", "Покупатель"),
        ("carrier", "Перевозчик"),
        ("terminal", "Терминал/элеватор"),
    ]
    for code, name in counterparty_types:
        _upsert_by_code(
            session,
            RefCounterpartyType,
            code,
            {
                "code": code,
                "name": name,
                "is_active": True,
            },
        )

    request_types = [
        ("purchase_from_supplier", "Закупка у поставщика"),
        ("sale_to_buyer", "Продажа покупателю"),
        ("logistics_request", "Запрос на логистику"),
        ("storage_request", "Запрос на хранение/перевалку"),
        ("export_request", "Экспортный запрос"),
        ("general_company_request", "Общий запрос о компании"),
    ]
    for code, name in request_types:
        _upsert_by_code(
            session,
            RefRequestType,
            code,
            {
                "code": code,
                "name": name,
                "is_active": True,
            },
        )

    stages = [
        ("new", "Новый", "lead", 10, False),
        ("draft", "Черновик", "lead", 20, False),
        ("partially_qualified", "Частично квалифицирован", "lead", 30, False),
        ("qualified", "Квалифицирован", "lead", 40, False),
        ("handed_to_manager", "Передан менеджеру", "lead", 50, False),
        ("closed", "Закрыт", "lead", 99, True),
        ("blocked", "Заблокирован", "lead", 98, True),
    ]
    for code, name, pipeline_code, sort_order, is_terminal in stages:
        _upsert_by_code(
            session,
            RefPipelineStage,
            code,
            {
                "code": code,
                "name": name,
                "pipeline_code": pipeline_code,
                "sort_order": sort_order,
                "is_terminal": is_terminal,
                "is_active": True,
            },
        )

    departments = [
        ("purchase", "Закупки"),
        ("sales", "Продажи"),
        ("logistics", "Логистика"),
        ("export", "ВЭД"),
        ("backoffice", "Бэк-офис"),
    ]
    for code, name in departments:
        _upsert_by_code(session, RefDepartment, code, {"code": code, "name": name, "is_active": True})

    manager_roles = [
        ("admin", "Администратор"),
        ("manager_purchase", "Менеджер закупок"),
        ("manager_sales", "Менеджер продаж"),
        ("manager_logistics", "Менеджер логистики"),
        ("manager_export", "Менеджер ВЭД"),
        ("supervisor", "Руководитель"),
    ]
    for code, name in manager_roles:
        _upsert_by_code(session, RefManagerRole, code, {"code": code, "name": name, "is_active": True})


def _ensure_company(session: Session) -> None:
    company = session.exec(select(CompanyProfile)).first()
    if company:
        return
    session.add(
        CompanyProfile(
            name='ООО "Петрохлеб-Кубань"',
            address="Краснодарский край, г. Краснодар, ул. Октябрьская, д. 8",
            phones="+7 (861) 992-13-61",
            email="mail@petrokhlebkuban.ru",
            services="Закупка, оптовая продажа зерновых и масличных, авто/жд/водная логистика, хранение и перевалка, экспорт",
            contacts_markdown=(
                "Коммерческий отдел: +7 (861) 992-13-61\n"
                "Отдел продаж: +7 (861) 992-13-61\n"
                "Логистика: +7 (861) 992-13-61\n"
                "ВЭД: +7 (861) 992-13-61"
            ),
        )
    )


def _ensure_admin_user(session: Session) -> None:
    login = os.getenv("ADMIN_USER", "admin")
    password = os.getenv("ADMIN_PASS", "315920")
    existing = session.exec(select(AdminUser).where(AdminUser.login == login)).first()
    if existing:
        existing.password_hash = _hash_password(password)
        existing.full_name = "Системный администратор"
        existing.role_code = "admin"
        existing.is_active = True
        existing.updated_at = datetime.utcnow()
        session.add(existing)
        return

    session.add(
        AdminUser(
            login=login,
            password_hash=_hash_password(password),
            full_name="Системный администратор",
            role_code="admin",
            is_active=True,
        )
    )


def _ensure_settings(session: Session) -> None:
    defaults = [
        (
            "assistant_tone",
            "Деловой, уверенный, лаконичный. Без воды и фантазий по цене/остаткам.",
            "assistant",
            "Тон ассистента",
            False,
        ),
        (
            "handoff_sla",
            "Менеджер связывается в течение 15 минут в рабочее время.",
            "process",
            "SLA обратной связи",
            False,
        ),
        (
            "llm_provider",
            "gigachat",
            "integration",
            "Текущий LLM провайдер",
            False,
        ),
    ]
    for key, value, group, description, secret in defaults:
        row = session.exec(select(AdminSetting).where(AdminSetting.setting_key == key)).first()
        if row:
            continue
        session.add(
            AdminSetting(
                setting_key=key,
                setting_value=value,
                setting_group=group,
                description=description,
                is_secret=secret,
            )
        )


def _ensure_quality_templates(session: Session) -> None:
    commodity = session.exec(select(RefCommodity).where(RefCommodity.code == "wheat")).first()
    if not commodity:
        return

    template = session.exec(
        select(CatalogQualityTemplate).where(CatalogQualityTemplate.template_code == "wheat_base_3")
    ).first()
    if template:
        return

    template = CatalogQualityTemplate(
        commodity_id=commodity.id,
        template_code="wheat_base_3",
        template_name="Пшеница базис 3 класс",
        is_default=True,
        is_active=True,
    )
    session.add(template)
    session.commit()
    session.refresh(template)

    params = {item.code: item.id for item in session.exec(select(RefQualityParameter)).all()}
    lines = [
        ("protein", ">=", 12.0, "", 10),
        ("moisture", "<=", 14.0, "", 20),
        ("gluten", ">=", 23.0, "", 30),
        ("foreign_matter", "<=", 2.0, "", 40),
    ]
    for code, op, numeric, text_value, sort_order in lines:
        param_id = params.get(code)
        if not param_id:
            continue
        session.add(
            CatalogQualityTemplateLine(
                quality_template_id=template.id,
                quality_parameter_id=param_id,
                comparison_operator=op,
                target_value_numeric=numeric,
                target_value_text=text_value,
                sort_order=sort_order,
            )
        )


def _ensure_price_policies(session: Session) -> None:
    if session.exec(select(CatalogPricePolicy)).first():
        return

    wheat = session.exec(select(RefCommodity).where(RefCommodity.code == "wheat")).first()
    sale = session.exec(select(RefRequestType).where(RefRequestType.code == "sale_to_buyer")).first()
    source = session.exec(select(RefRegion).where(RefRegion.code == "ru-kk")).first()
    destination = session.exec(select(RefRegion).where(RefRegion.code == "ru-nov-port")).first()
    road = session.exec(select(RefTransportMode).where(RefTransportMode.code == "road")).first()

    session.add(
        CatalogPricePolicy(
            code="sale_wheat_kk_novorossiysk",
            name="Продажа пшеницы 3 класс: КК -> Новороссийск",
            commodity_id=wheat.id if wheat else None,
            request_type_id=sale.id if sale else None,
            source_region_id=source.id if source else None,
            destination_region_id=destination.id if destination else None,
            transport_mode_id=road.id if road else None,
            min_volume=100,
            max_volume=5000,
            pricing_rule_text="Индикатив рассчитывается от базиса, качества и логистического плеча. Фиксация через менеджера.",
            manager_note="Для экспортных партий учитывать окно погрузки и судовую линию.",
            is_active=True,
            valid_from=datetime.utcnow(),
            valid_to=datetime.utcnow() + timedelta(days=365),
        )
    )


def _ensure_stock_placeholders(session: Session) -> None:
    if session.exec(select(CatalogStockPlaceholder)).first():
        return

    wheat = session.exec(select(RefCommodity).where(RefCommodity.code == "wheat")).first()
    barley = session.exec(select(RefCommodity).where(RefCommodity.code == "barley")).first()
    corn = session.exec(select(RefCommodity).where(RefCommodity.code == "corn")).first()
    kk = session.exec(select(RefRegion).where(RefRegion.code == "ru-kk")).first()
    ro = session.exec(select(RefRegion).where(RefRegion.code == "ru-ro")).first()

    candidates = [
        (wheat, kk, 4200, "Внутренний пул"),
        (barley, ro, 2600, "Партнерский пул"),
        (corn, kk, 3100, "Экспортный пул"),
    ]
    for commodity, region, volume, owner in candidates:
        if not commodity or not region:
            continue
        session.add(
            CatalogStockPlaceholder(
                commodity_id=commodity.id,
                location_region_id=region.id,
                volume_available=volume,
                unit="тонна",
                availability_status="open",
                owner_label=owner,
                transport_access_text="Авто/ЖД",
                comment="Тестовый лот для прототипа",
                is_active=True,
            )
        )


def _ensure_knowledge(session: Session) -> None:
    articles = [
        (
            "company_profile",
            "Профиль компании",
            "company",
            None,
            None,
            "ООО «Петрохлеб-Кубань» работает как B2B трейдер и логистический оператор по зерновым и масличным.",
            "Работаем в закупке, продаже, логистике, хранении и ВЭД.",
            10,
        ),
        (
            "products_scope",
            "Какие культуры обрабатываем",
            "products",
            None,
            None,
            "Работаем с пшеницей, ячменем, кукурузой, подсолнечником, горохом, нутом, льном и кориандром.",
            "Основные культуры: пшеница, ячмень, кукуруза и масличные.",
            20,
        ),
        (
            "logistics_modes",
            "Логистика",
            "logistics",
            None,
            None,
            "Организуем авто, ж/д и водную логистику, включая мультимодальные плечи до порта.",
            "Подбираем маршрут и транспорт под объем и срок.",
            30,
        ),
        (
            "storage_transshipment",
            "Хранение и перевалка",
            "storage",
            None,
            None,
            "Доступны хранение, перевалка и подготовка партий к отгрузке на внутренний рынок и экспорт.",
            "Можем взять партию на хранение и организовать отгрузку по графику.",
            40,
        ),
        (
            "pricing_policy",
            "Как формируется цена",
            "sales",
            None,
            None,
            "Цена не является публичной фиксированной: зависит от культуры, качества, объема, базиса, логистики, сроков и условий оплаты.",
            "Даем коммерческое предложение после фиксации ключевых параметров сделки.",
            50,
        ),
    ]
    for code, title, group, req_id, commodity_id, markdown, short_answer, sort_order in articles:
        row = session.exec(select(KnowledgeArticle).where(KnowledgeArticle.code == code)).first()
        if row:
            continue
        session.add(
            KnowledgeArticle(
                code=code,
                title=title,
                article_group=group,
                request_type_id=req_id,
                commodity_id=commodity_id,
                content_markdown=markdown,
                short_answer=short_answer,
                is_active=True,
                sort_order=sort_order,
                updated_at=datetime.utcnow(),
            )
        )


def seed_defaults(session: Session) -> None:
    _ensure_reference_catalogs(session)
    session.commit()

    _ensure_company(session)
    _ensure_admin_user(session)
    _ensure_settings(session)
    session.commit()

    _ensure_quality_templates(session)
    _ensure_price_policies(session)
    _ensure_stock_placeholders(session)
    _ensure_knowledge(session)
    session.commit()
