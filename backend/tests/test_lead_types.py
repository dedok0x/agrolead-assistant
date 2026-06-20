import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_lead_types_v7.db").resolve()
if DB_FILE.exists():
    DB_FILE.unlink()

os.environ.setdefault("DATABASE_URL", f"sqlite:///{DB_FILE.as_posix()}")
os.environ.setdefault("TOXIC_STRICT_MODE", "1")
os.environ.setdefault("LLM_PROVIDER", "gigachat")
os.environ.setdefault("ADMIN_PASS", "test-admin-password")

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.main import app, llm_service, startup
from tests._agent_mock import install_fake_gigachat


class LeadTypesCases(unittest.TestCase):
    """Каждый тип обращения формирует корректную заявку (или не формирует)."""

    def setUp(self):
        startup()
        self.client = TestClient(app)
        self._auth_key = llm_service.gigachat_client.auth_key
        install_fake_gigachat(llm_service)

    def tearDown(self):
        llm_service.gigachat_client.auth_key = self._auth_key

    def _send(self, text, client_id, session_id=None):
        response = self.client.post(
            "/api/chat",
            json={"text": text, "client_id": client_id, "session_id": session_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _admin_token(self):
        login = self.client.post(
            "/api/v1/admin/login",
            json={"username": os.getenv("ADMIN_USER", "admin"), "password": os.getenv("ADMIN_PASS", "test-admin-password")},
        )
        self.assertEqual(login.status_code, 200)
        return login.json()["token"]

    def test_buy_request_creates_lead(self):
        p = self._send(
            "Здравствуйте, хочу купить 100 тонн пшеницы с доставкой в Краснодар. Телефон +7 900 111-22-33.",
            client_id="lt-buy",
        )
        self.assertEqual(p["known_facts"]["request_type"], "buy_grain")
        self.assertEqual(p["request_type"], "sale_to_buyer")
        self.assertEqual(p["known_facts"]["product"], "пшеница")
        self.assertEqual(p["known_facts"]["volume"], "100 тонн")
        self.assertEqual(p["known_facts"]["region"], "Краснодарский край")
        self.assertIn("contact", p["known_facts"])
        self.assertEqual(p["missing_fields"], ["timing"])
        self.assertTrue(p["lead_id"])

    def test_sell_request_creates_lead_with_quality(self):
        p = self._send(
            "Есть 400 тонн пшеницы 3 класса в Краснодарском крае, готовы продать, звоните +7 918 222-33-44.",
            client_id="lt-sell",
        )
        self.assertEqual(p["known_facts"]["request_type"], "sell_grain")
        self.assertEqual(p["request_type"], "purchase_from_supplier")
        self.assertEqual(p["known_facts"]["quality_class"], "3 класс")
        self.assertEqual(p["known_facts"]["volume"], "400 тонн")
        self.assertTrue(p["lead_id"])

    def test_logistics_request_with_route(self):
        p = self._send(
            "Нужно перевезти 250 тонн ячменя из Ростовской области в Новороссийск на следующей неделе.",
            client_id="lt-logist",
        )
        self.assertEqual(p["known_facts"]["request_type"], "logistics")
        self.assertEqual(p["request_type"], "logistics_request")
        self.assertEqual(p["known_facts"]["product"], "ячмень")
        self.assertEqual(p["known_facts"]["volume"], "250 тонн")
        self.assertEqual(p["known_facts"]["region"], "Ростовская область -> Новороссийск")
        self.assertEqual(p["known_facts"]["route_from"], "Ростовская область")
        self.assertEqual(p["known_facts"]["route_to"], "Новороссийск")
        self.assertIn("недел", p["known_facts"]["timing"])
        self.assertEqual(p["missing_fields"], ["contact"])
        self.assertEqual(p["next_action"], "ask_contact")

    def test_storage_request(self):
        p = self._send(
            "Интересует хранение 300 тонн подсолнечника в Краснодаре, срок два месяца.",
            client_id="lt-storage",
        )
        self.assertEqual(p["known_facts"]["request_type"], "storage")
        self.assertEqual(p["request_type"], "storage_request")
        self.assertEqual(p["known_facts"]["product"], "подсолнечник")
        self.assertEqual(p["known_facts"]["region"], "Краснодарский край")
        self.assertIn("месяц", p["known_facts"]["timing"])
        self.assertEqual(p["missing_fields"], ["contact"])

    def test_faq_question_does_not_create_lead(self):
        p = self._send("Какие документы нужны для поставки зерна?", client_id="lt-faq")
        self.assertIsNone(p["lead_id"])
        self.assertEqual(p["status"], "faq_only")
        self.assertNotIn("{", p["text"])  # без служебной утечки
        self.assertNotIn("какую культуру", p["text"].lower())

    def test_faq_then_intent_converts_to_lead(self):
        p = self._send("Какие документы нужны для поставки зерна?", client_id="lt-faq2")
        session_id = p["session_id"]
        self.assertIsNone(p["lead_id"])
        p = self._send("Хочу продать 200 тонн кукурузы", session_id=session_id, client_id="lt-faq2")
        self.assertTrue(p["lead_id"])
        self.assertEqual(p["known_facts"]["request_type"], "sell_grain")

    def test_smalltalk_does_not_create_lead(self):
        p = self._send("привет", client_id="lt-smalltalk")
        self.assertIsNone(p["lead_id"])
        self.assertEqual(p["status"], "faq_only")

    def test_irrelevant_request_is_not_a_lead(self):
        # Не по теме: GigaChat возвращает в тему, заявка не создаётся, утечки нет.
        p = self._send("Расскажи анекдот про погоду", client_id="lt-irrelevant")
        self.assertIsNone(p["lead_id"])
        self.assertTrue(p["text"])
        self.assertNotIn("{", p["text"])

    def test_toxic_request_is_blocked_without_lead(self):
        p = self._send("иди на хуй", client_id="lt-toxic")
        self.assertEqual(p["provider"], "guardrails")
        self.assertEqual(p["state"], "blocked")
        self.assertIsNone(p["lead_id"])

    def test_incomplete_request_stays_draft_and_asks_next_field(self):
        p = self._send("Хочу купить пшеницу", client_id="lt-incomplete")
        self.assertEqual(p["status"], "draft")
        self.assertEqual(p["next_action"], "ask_volume")
        self.assertLess(p["qualification_score"], 100)

    def test_hot_flag_for_large_volume(self):
        p = self._send(
            "Продажа пшеницы 2000 тонн из Краснодара, срочно, +79001234567",
            client_id="lt-hot",
        )
        token = self._admin_token()
        leads = self.client.get("/api/v1/leads", headers={"x-admin-token": token}).json()
        lead = next(item for item in leads if item["id"] == p["lead_id"])
        self.assertTrue(lead["hot_flag"])
        self.assertEqual(lead["priority_code"], "high")

    def test_logistics_route_resolves_crm_regions(self):
        p = self._send(
            "Нужно перевезти 250 тонн ячменя из Краснодара в Новороссийск завтра, контакт +79281112233",
            client_id="lt-route-crm",
        )
        token = self._admin_token()
        leads = self.client.get("/api/v1/leads", headers={"x-admin-token": token}).json()
        lead = next(item for item in leads if item["id"] == p["lead_id"])
        item = lead["lead_item"]
        self.assertIsNotNone(item)
        self.assertTrue(item["source_region_id"])
        self.assertTrue(item["destination_region_id"])
        self.assertNotEqual(item["source_region_id"], item["destination_region_id"])


if __name__ == "__main__":
    unittest.main()
