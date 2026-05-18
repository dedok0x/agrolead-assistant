import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_telegram_webhook_v7.db").resolve()
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

from app.main import app, llm_service, startup, telegram_service


class TelegramWebhookCases(unittest.TestCase):
    def setUp(self):
        startup()
        self.client = TestClient(app)
        self._token = telegram_service.token
        self._auth_key = llm_service.gigachat_client.auth_key
        telegram_service.token = ""
        llm_service.gigachat_client.auth_key = ""

    def tearDown(self):
        telegram_service.token = self._token
        llm_service.gigachat_client.auth_key = self._auth_key

    def test_webhook_without_token_does_not_crash(self):
        response = self.client.post(
            "/api/integrations/telegram/webhook",
            json={"message": {"chat": {"id": 9001}, "from": {"id": 9001}, "text": "/start"}},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["enabled"])
        self.assertTrue(payload["handled"])

    def test_text_message_maps_to_conversation_service(self):
        response = self.client.post(
            "/api/integrations/telegram/webhook",
            json={"message": {"chat": {"id": 9002}, "from": {"id": 9002}, "text": "Продажа"}},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["handled"])
        self.assertTrue(payload.get("session_id"))
        self.assertTrue(payload.get("lead_id"))

    def test_callback_button_maps_to_text(self):
        response = self.client.post(
            "/api/integrations/telegram/webhook",
            json={
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 9003},
                    "data": "sell_grain",
                    "message": {"chat": {"id": 9003}},
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["handled"])
        self.assertTrue(payload.get("session_id"))


if __name__ == "__main__":
    unittest.main()
