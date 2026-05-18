import json
import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_conversation_service_v7.db").resolve()
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


class ConversationServiceCases(unittest.TestCase):
    def setUp(self):
        startup()
        self.client = TestClient(app)
        self._auth_key = llm_service.gigachat_client.auth_key
        llm_service.gigachat_client.auth_key = ""

    def tearDown(self):
        llm_service.gigachat_client.auth_key = self._auth_key

    def test_web_channel_uses_unified_flow(self):
        response = self.client.post(
            "/api/chat",
            json={"text": "Продажа", "client_id": "v7-web-1", "source_channel": "web_widget"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source_channel"], "web_widget")
        self.assertEqual(payload["known_facts"]["request_type"], "sell_grain")
        self.assertEqual(payload["next_action"], "ask_product")

    def test_telegram_channel_uses_unified_flow(self):
        response = self.client.post(
            "/api/chat",
            json={"text": "Купить пшеницу 400 тонн", "client_id": "telegram:101", "source_channel": "telegram", "external_user_id": "101"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source_channel"], "telegram")
        self.assertEqual(payload["known_facts"]["request_type"], "buy_grain")
        self.assertEqual(payload["known_facts"]["product"], "пшеница")
        self.assertIn("region", payload["missing_fields"])

    def test_fallback_works_without_gigachat(self):
        response = self.client.post(
            "/api/chat",
            json={"text": "Продажа пшеницы 400 тонн Краснодарский край", "client_id": "v7-fallback"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "fallback")
        self.assertTrue(payload["text"])

    def test_stream_final_event_contains_v7_state(self):
        response = self.client.post(
            "/api/chat/stream",
            json={"text": "Продажа пшеницы 400 тонн Краснодарский край", "client_id": "v7-stream"},
        )
        self.assertEqual(response.status_code, 200)
        final = json.loads([line for line in response.text.splitlines() if line][-1])
        self.assertTrue(final["done"])
        self.assertIn("known_facts", final)
        self.assertIn("missing_fields", final)
        self.assertIn("qualification_score", final)
        self.assertIn("next_action", final)


if __name__ == "__main__":
    unittest.main()
