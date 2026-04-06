import os
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock

DB_FILE = pathlib.Path("./test_integration_dialogue_v6.db").resolve()
if DB_FILE.exists():
    DB_FILE.unlink()

os.environ.setdefault("DATABASE_URL", f"sqlite:///{DB_FILE.as_posix()}")
os.environ.setdefault("TOXIC_STRICT_MODE", "1")
os.environ.setdefault("LLM_PROVIDER", "gigachat")
os.environ.setdefault("GIGACHAT_AUTH_KEY", "test-auth-key")
os.environ.setdefault("GIGACHAT_VERIFY_SSL", "0")

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.main import app, llm_service
from app.sales_logic import detect_request_type, extract_facts, parse_contact_name_or_company


class IntegrationDialogueCases(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self._orig_chat_completion = llm_service.gigachat_client.chat_completion
        llm_service.gigachat_client.chat_completion = AsyncMock(
            return_value=(
                "Принял данные, заявку фиксирую. Уточним следующий коммерческий параметр и передам менеджеру.",
                "GigaChat-2",
            )
        )

    def tearDown(self) -> None:
        llm_service.gigachat_client.chat_completion = self._orig_chat_completion

    def _admin_token(self) -> str:
        login = self.client.post(
            "/api/v1/admin/login",
            json={"username": os.getenv("ADMIN_USER", "admin"), "password": os.getenv("ADMIN_PASS", "315920")},
        )
        self.assertEqual(login.status_code, 200)
        return login.json()["token"]

    def test_request_type_detection(self):
        self.assertEqual(detect_request_type("Хотим продать подсолнечник 500 тонн"), "purchase_from_supplier")
        self.assertEqual(detect_request_type("Нужна покупка пшеницы 3 класс"), "sale_to_buyer")
        self.assertEqual(detect_request_type("Нужны вагоны из Краснодара в Новороссийск"), "logistics_request")
        self.assertEqual(detect_request_type("Нужен экспорт через порт Новороссийск"), "export_request")
        self.assertEqual(detect_request_type("Пшеница 200 тонн, Краснодар"), "general_company_request")

    def test_contact_name_filtering(self):
        self.assertEqual(parse_contact_name_or_company("Нужна логистика авто из Краснодара в Новороссийск"), "")
        self.assertTrue(parse_contact_name_or_company("ООО АгроПлюс, контакт +79001112233"))

    def test_fact_extraction_basic(self):
        commodity_map = {"пшеница": 1}
        region_map = {"краснодар": 10}
        facts = extract_facts("Пшеница 3 класс, объем 200 тонн, Краснодар, контакт +79001234567", commodity_map, region_map)
        self.assertIn("commodity_id", facts)
        self.assertIn("volume_value", facts)
        self.assertIn("source_region_id", facts)
        self.assertIn("contact_phone_or_telegram_or_email", facts)

        no_dict_region = extract_facts("Самара, 2 тонны", commodity_map, {})
        self.assertIn("destination_region_id_or_port", no_dict_region)

    def test_supplier_and_buyer_and_logistics_flows_create_leads(self):
        # ambiguous -> assistant should keep general type and clarify
        r0 = self.client.post(
            "/api/v1/chat",
            json={"text": "Пшеница 200 тонн, Краснодар", "client_id": "gen-1"},
        )
        self.assertEqual(r0.status_code, 200)
        self.assertEqual(r0.json().get("request_type"), "general_company_request")

        # supplier
        r1 = self.client.post(
            "/api/v1/chat",
            json={"text": "Хотим продать пшеницу 3 класс 400 тонн из Краснодара, контакт +79001112233", "client_id": "sup-1"},
        )
        self.assertEqual(r1.status_code, 200)
        p1 = r1.json()
        self.assertIn(p1.get("request_type"), {"purchase_from_supplier", "sale_to_buyer"})
        self.assertIn(p1.get("status"), {"draft", "partially_qualified", "qualified"})

        # buyer
        r2 = self.client.post(
            "/api/v1/chat",
            json={"text": "Нужна покупка ячменя 250 тонн в Ростов, контакты ООО АгроПлюс +79004445566", "client_id": "buyer-1"},
        )
        self.assertEqual(r2.status_code, 200)
        p2 = r2.json()
        self.assertIn(p2.get("request_type"), {"sale_to_buyer", "purchase_from_supplier"})

        # logistics
        r3 = self.client.post(
            "/api/v1/chat",
            json={"text": "Нужна логистика авто из Краснодара в Новороссийск 300 тонн, контакт @log_user_123", "client_id": "log-1"},
        )
        self.assertEqual(r3.status_code, 200)
        p3 = r3.json()
        self.assertEqual(p3.get("request_type"), "logistics_request")

        token = self._admin_token()
        leads_response = self.client.get("/api/v1/leads", headers={"x-admin-token": token})
        self.assertEqual(leads_response.status_code, 200)
        leads = leads_response.json()
        self.assertGreaterEqual(len(leads), 3)

    def test_toxic_blocked(self):
        response = self.client.post("/api/v1/chat", json={"text": "иди на хуй", "client_id": "tox-1"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("provider"), "guardrails")
        self.assertEqual(payload.get("state"), "blocked")

    def test_admin_nomenclature_crud_and_knowledge_crud_and_lead_assignment(self):
        token = self._admin_token()

        # commodities CRUD
        create_commodity = self.client.post(
            "/api/v1/catalog/commodities",
            headers={"x-admin-token": token},
            json={
                "code": "millet",
                "name": "Просо",
                "full_name": "Просо продовольственное",
                "commodity_group": "grain",
                "unit_of_measure_default": "тонна",
                "is_active": True,
                "sort_order": 999,
            },
        )
        self.assertEqual(create_commodity.status_code, 200)
        commodity_id = create_commodity.json()["id"]

        update_commodity = self.client.put(
            f"/api/v1/catalog/commodities/{commodity_id}",
            headers={"x-admin-token": token},
            json={"name": "Просо обновленное"},
        )
        self.assertEqual(update_commodity.status_code, 200)

        # knowledge CRUD
        create_article = self.client.post(
            "/api/v1/admin/knowledge",
            headers={"x-admin-token": token},
            json={
                "code": "faq_test_article",
                "title": "Тестовая статья",
                "article_group": "faq",
                "content_markdown": "Контент",
                "short_answer": "Короткий ответ",
                "is_active": True,
                "sort_order": 1000,
            },
        )
        self.assertEqual(create_article.status_code, 200)
        article_id = create_article.json()["id"]

        update_article = self.client.put(
            f"/api/v1/admin/knowledge/{article_id}",
            headers={"x-admin-token": token},
            json={"short_answer": "Обновлено"},
        )
        self.assertEqual(update_article.status_code, 200)

        # create lead and assign
        chat = self.client.post(
            "/api/v1/chat",
            json={"text": "Нужна кукуруза 150 тонн в Краснодар, контакт +79007778899", "client_id": "assign-1"},
        )
        self.assertEqual(chat.status_code, 200)
        lead_id = chat.json()["lead_id"]

        assign = self.client.put(
            f"/api/v1/leads/{lead_id}",
            headers={"x-admin-token": token},
            json={"status_code": "handed_to_manager", "manager_comment": "Назначено в отдел продаж"},
        )
        self.assertEqual(assign.status_code, 200)
        self.assertTrue(assign.json().get("ok"))

    def test_admin_users_mask_password_hash(self):
        token = self._admin_token()
        users = self.client.get("/api/v1/admin/users", headers={"x-admin-token": token})
        self.assertEqual(users.status_code, 200)
        payload = users.json()
        self.assertTrue(payload)
        self.assertNotIn("password_hash", payload[0])

    def test_admin_login_returns_rotating_session_tokens(self):
        login1 = self.client.post(
            "/api/v1/admin/login",
            json={"username": os.getenv("ADMIN_USER", "admin"), "password": os.getenv("ADMIN_PASS", "315920")},
        )
        self.assertEqual(login1.status_code, 200)

        login2 = self.client.post(
            "/api/v1/admin/login",
            json={"username": os.getenv("ADMIN_USER", "admin"), "password": os.getenv("ADMIN_PASS", "315920")},
        )
        self.assertEqual(login2.status_code, 200)
        self.assertNotEqual(login1.json().get("token"), login2.json().get("token"))

    def test_session_owner_mismatch_returns_403(self):
        first = self.client.post(
            "/api/v1/chat",
            json={"text": "Продажа пшеницы 100 тонн, Краснодар", "client_id": "owner-1"},
        )
        self.assertEqual(first.status_code, 200)
        session_id = first.json().get("session_id")
        self.assertTrue(session_id)

        hijack = self.client.post(
            "/api/v1/chat",
            json={"text": "Попытка продолжить чужую сессию", "client_id": "owner-2", "session_id": session_id},
        )
        self.assertEqual(hijack.status_code, 403)

    def test_guardrails_replies_have_variation(self):
        replies = []
        for _ in range(3):
            response = self.client.post("/api/v1/chat", json={"text": "иди на хуй", "client_id": "tox-var"})
            self.assertEqual(response.status_code, 200)
            replies.append(response.json().get("text", ""))
        self.assertGreaterEqual(len(set(replies)), 2)

    def test_catalog_payload_alignment_for_quality_and_price(self):
        token = self._admin_token()

        quality = self.client.post(
            "/api/v1/catalog/quality-templates",
            headers={"x-admin-token": token},
            json={
                "code": "compat_quality",
                "name": "Совместимый шаблон",
                "commodity_id": 1,
                "description": "legacy payload",
                "is_active": True,
                "lines": [{"parameter_code": "protein", "operator": ">=", "target_value": "12.5", "unit": "%"}],
            },
        )
        self.assertEqual(quality.status_code, 200)
        quality_payload = quality.json()
        self.assertEqual(quality_payload.get("template_code"), "compat_quality")
        self.assertTrue(isinstance(quality_payload.get("lines"), list))

        policy = self.client.post(
            "/api/v1/catalog/price-policies",
            headers={"x-admin-token": token},
            json={
                "code": "compat_policy",
                "name": "Совместимая политика",
                "commodity_id": 1,
                "region_id": 1,
                "currency_code": "RUB",
                "price_formula_text": "base + logistics",
                "is_active": True,
            },
        )
        self.assertEqual(policy.status_code, 200)
        self.assertIn("pricing_rule_text", policy.json())


if __name__ == "__main__":
    unittest.main()
