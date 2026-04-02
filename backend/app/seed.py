from sqlmodel import Session, select

from .models import CompanyProfile, PromptCategory, Scenario, ProductItem, ScenarioTemplate


DEFAULT_CONTACTS = """КОММЕРЧЕСКИЙ ОТДЕЛ:
Бондаренко Николай Николаевич — +7 (861) 992 13 61 (доб. 105), bondarenko@petrokhlebkuban.ru
Владимиров Владимир Васильевич — +7 (861) 992 13 61 (доб. 106), vladimirov@petrokhlebkuban.ru
Ульянов Александр Владимирович — +7 (861) 992 13 61 (доб. 125), ulyanov@petrokhlebkuban.ru

ОТДЕЛ ПРОДАЖ:
Вехов Олег Владимирович — +7 (861) 992 13 61 (доб. 104), bexob@mail.ru
Чередниченко Алексей Викторович — +7 (861) 992 13 61 (доб. 107), cherednichenko@petrokhlebkuban.ru

ОТДЕЛ АВТОЛОГИСТИКИ:
Струцкий Валерий Владимирович — +7 (861) 992 13 61 (доб. 102), strutskiy@petrokhlebkuban.ru
Голубцова Анастасия Владимировна — +7 (861) 992 13 61 (доб. 129), zd@petrokhlebkuban.ru

ОТДЕЛ ВЭД:
Корбула Александр Александрович — +7 (861) 992 13 61, korbula@petrokhlebkuban.ru
Моздор София Евгеньевна — +7 (861) 992 13 61 (доб. 110), pobegaylenko@petrokhlebkuban.ru, execution@petrokhlebkuban.ru

ДИРЕКТОР:
Фисик Максим Васильевич — +7 (861) 992 13 61, mail@petrokhlebkuban.ru
"""


def seed_defaults(session: Session) -> None:
    profile = session.exec(select(CompanyProfile)).first()
    if not profile:
        session.add(
            CompanyProfile(
                name='ООО "Петрохлеб-Кубань"',
                address='350063 Краснодарский край, г. Краснодар, ул. Октябрьская, д. 8, 2 этаж',
                phones='+7 861 992 13 61, +7 861 992 13 63',
                email='mail@petrokhlebkuban.ru',
                services='Закупка, Логистика, Хранение, Продажа, Внешнеэкономическая деятельность',
                contacts_markdown=DEFAULT_CONTACTS,
            )
        )

    prompt_defaults = {
        "identity": "Ты — клиентский ассистент ООО «Петрохлеб-Кубань». Твоя роль: быстро и по-человечески доводить клиента до квалифицированной заявки.",
        "scope": "Только зерновая тематика: пшеница, ячмень, кукуруза, классы качества, объем, регион, срок, логистика, оформление заявки.",
        "safety": "Если мат/токсичность: короткий ответ без вежливой квалификации. Если кибер/вред — жёсткий отказ и возврат к теме зерна.",
        "style": "Стиль: живой, прямой, кубанский. Без канцелярщины. Ответы короткие и по делу, без выдуманных фактов.",
        "lead_capture": "Собирай строго по state-machine: товар → класс → объем → регион → срок → контакт.",
    }
    for key, content in prompt_defaults.items():
        exists = session.exec(select(PromptCategory).where(PromptCategory.key == key)).first()
        if not exists:
            session.add(PromptCategory(key=key, title=key, content=content))

    if not session.exec(select(Scenario)).first():
        session.add_all(
            [
                Scenario(title='Кто вы и чем занимаетесь?', description='Короткое представление компании и ролей ассистента.'),
                Scenario(title='Какие товары в наличии?', description='Перечень номенклатуры + уточнение класса/объема.'),
                Scenario(title='Какая цена и минимальный объем?', description='Диапазон цены + запрос параметров сделки.'),
                Scenario(title='Какой срок и способ доставки?', description='Логистика: авто/жд/вода + сроки.'),
                Scenario(title='Как оформить заявку?', description='Запрос контактов и передача менеджеру.'),
            ]
        )

    if not session.exec(select(ProductItem)).first():
        session.add_all(
            [
                ProductItem(name='Пшеница 3 класс, продовольственная', culture='Пшеница', grade='3 класс', price_from=15000, price_to=17200, stock_tons=4200, quality='Клейковина 23-25%, натура от 760', location='Краснодарский край'),
                ProductItem(name='Пшеница 4 класс, фуражная', culture='Пшеница', grade='4 класс', price_from=13600, price_to=14900, stock_tons=6100, quality='Протеин 10.5-11.5%', location='Краснодарский край'),
                ProductItem(name='Ячмень кормовой', culture='Ячмень', grade='Кормовой', price_from=12100, price_to=13400, stock_tons=2800, quality='Влажность до 14.5%', location='Ростовская область'),
                ProductItem(name='Кукуруза 3 класс', culture='Кукуруза', grade='3 класс', price_from=12800, price_to=14100, stock_tons=3500, quality='Влажность до 14%, сорная примесь до 2%', location='Ставропольский край'),
                ProductItem(name='Кукуруза фуражная', culture='Кукуруза', grade='Фуражная', price_from=11600, price_to=12900, stock_tons=2400, quality='Базис по ГОСТ', location='Краснодарский край'),
            ]
        )

    if not session.exec(select(ScenarioTemplate)).first():
        session.add_all(
            [
                ScenarioTemplate(
                    title='Квалификация по цене и наличию',
                    goal='Собрать товар, класс, объем, регион поставки и срок отгрузки',
                    starter_message='Подскажите, пожалуйста, какую культуру и класс вы рассматриваете, а также ориентировочный объем в тоннах?'
                ),
                ScenarioTemplate(
                    title='Логистика и срок поставки',
                    goal='Уточнить направление, транспорт, дату и контакт для просчета',
                    starter_message='Уточните регион доставки, желаемый срок и предпочтительный способ перевозки (авто/жд/вода).'
                ),
                ScenarioTemplate(
                    title='Формализация заявки',
                    goal='Получить контакт и передать лид менеджеру',
                    starter_message='Чтобы передать заявку менеджеру, оставьте контакт: имя, телефон или email.'
                ),
                ScenarioTemplate(
                    title='Возражение по цене',
                    goal='Сохранить интерес и перевести в заявку с альтернативными условиями',
                    starter_message='Можем предложить варианты по объему, базису и сроку оплаты. Уточните, какой диапазон цены вам комфортен?'
                ),
            ]
        )

    session.commit()

