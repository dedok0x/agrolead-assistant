import json
import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_api_contract_v7.db").resolve()
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


class ApiContractConsistencyCases(unittest.TestCase):
    """JSON, NDJSON и admin API согласованы между собой."""

    def setUp(self):
        startup()
        self.client = TestClient(app)
        self._auth_key = llm_service.gigachat_client.auth_key
        install_fake_gigachat(llm_service)

    def tearDown(self):
        llm_service.gigachat_client.auth_key = self._auth_key

    def _admin_token(self):
        login = self.client.post(
            "/api/v1/admin/login",
            json={"username": os.getenv("ADMIN_USER", "admin"), "password": os.getenv("ADMIN_PASS", "test-admin-password")},
        )
        self.assertEqual(login.status_code, 200)
        return login.json()["token"]

    def test_json_response_contains_full_contract(self):
        response = self.client.post(
            "/api/chat",
            json={"text": "Продажа пшеницы 400 тонн из Краснодара", "client_id": "contract-json"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        for key in [
            "session_id", "lead_id", "status", "stage", "known_facts", "uncertain_facts",
            "missing_fields", "qualification_score", "next_action", "text",
        ]:
            self.assertIn(key, payload, key)

    def test_json_and_stream_have_same_final_state(self):
        text = "Продажа пшеницы 400 тонн из Краснодара, контакт +79001112233, срок июнь"
        json_payload = self.client.post(
            "/api/chat", json={"text": text, "client_id": "contract-a"}
        ).json()

        stream = self.client.post(
            "/api/chat/stream", json={"text": text, "client_id": "contract-b"}
        )
        final = json.loads([line for line in stream.text.splitlines() if line.strip()][-1])

        for key in ["status", "stage", "known_facts", "missing_fields", "qualification_score", "next_action", "request_type"]:
            self.assertEqual(json_payload[key], final[key], key)
        self.assertTrue(final["done"])
        self.assertEqual(final["text"], final["token"])
        self.assertTrue(final["text"])

    def test_health_check_reports_db_ok(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["db_ok"])

    def test_admin_login_and_leads_registry(self):
        chat = self.client.post(
            "/api/chat",
            json={"text": "Продажа кукурузы 700 тонн из Ростова, +79286665544, срочно", "client_id": "contract-lead"},
        ).json()
        token = self._admin_token()
        leads = self.client.get("/api/v1/leads", headers={"x-admin-token": token})
        self.assertEqual(leads.status_code, 200)
        target = next(item for item in leads.json() if item["id"] == chat["lead_id"])
        self.assertEqual(target["status_code"], chat["status"])
        self.assertIn("qualification_score", target)
        self.assertIsNotNone(target["lead_item"])
        self.assertIsNotNone(target["contact_snapshot"])
        self.assertTrue(target["contact_snapshot"]["phone"])

    def test_admin_workspace_returns_dialogue_state(self):
        chat = self.client.post(
            "/api/chat",
            json={"text": "Хочу купить ячмень 250 тонн", "client_id": "contract-ws"},
        ).json()
        self.client.post(
            "/api/chat",
            json={"text": "в Новороссийск", "client_id": "contract-ws", "session_id": chat["session_id"]},
        )
        token = self._admin_token()
        ws = self.client.get(
            f"/api/v1/admin/leads/{chat['lead_id']}/workspace", headers={"x-admin-token": token}
        )
        self.assertEqual(ws.status_code, 200)
        payload = ws.json()
        self.assertTrue(payload["sessions"])
        self.assertGreaterEqual(len(payload["messages"]), 4)
        fact_keys = {fact["fact_key"] for fact in payload["facts"]}
        self.assertIn("product", fact_keys)
        self.assertIn("volume", fact_keys)
        missing_codes = {row["field_code"] for row in payload["missing_fields"] if not row["is_collected"]}
        self.assertIn("timing", missing_codes)
        self.assertIn("contact", missing_codes)
        self.assertNotIn("product", missing_codes)

    def test_admin_session_detail_returns_messages_and_facts(self):
        chat = self.client.post(
            "/api/chat",
            json={"text": "Продажа пшеницы 150 тонн", "client_id": "contract-session"},
        ).json()
        token = self._admin_token()
        detail = self.client.get(
            f"/api/v1/admin/chat-sessions/{chat['session_id']}", headers={"x-admin-token": token}
        )
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(len(payload["messages"]), 2)
        self.assertTrue(payload["facts"])

    def test_unauthorized_admin_request_rejected(self):
        response = self.client.get("/api/v1/leads", headers={"x-admin-token": "wrong-token"})
        self.assertEqual(response.status_code, 401)

    def test_empty_text_is_controlled_error_on_both_endpoints(self):
        for path in ["/api/chat", "/api/chat/stream", "/api/v1/chat"]:
            response = self.client.post(path, json={"text": "", "client_id": "contract-empty"})
            self.assertEqual(response.status_code, 400, path)


if __name__ == "__main__":
    unittest.main()
