import json
import os
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_chat_stream.db")
os.environ.setdefault("TOXIC_STRICT_MODE", "1")
os.environ.setdefault("LLM_PROVIDER", "gigachat")
os.environ.setdefault("GIGACHAT_AUTH_KEY", "test-auth-key")

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.main import app, llm_service


class ChatStreamCases(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self._orig_chat_completion = llm_service.gigachat_client.chat_completion

        llm_service.gigachat_client.chat_completion = AsyncMock(
            return_value=(
                "Позиция есть в наличии. Для точного расчета дайте объем и регион доставки.",
                "GigaChat-2",
            )
        )

    def tearDown(self) -> None:
        llm_service.gigachat_client.chat_completion = self._orig_chat_completion

    def test_chat_stream_returns_done(self):
        response = self.client.post(
            "/api/chat/stream",
            json={"text": "Привет", "client_id": "test-stream"},
            headers={"accept": "application/x-ndjson"},
        )
        self.assertEqual(response.status_code, 200)
        lines = [line for line in response.text.splitlines() if line.strip()]
        self.assertTrue(lines)
        payload = json.loads(lines[-1])
        self.assertTrue(payload.get("done"))
        self.assertIn("session_id", payload)

    def test_dry_run_uses_gigachat_when_available(self):
        response = self.client.post("/api/chat/dry-run", json={"text": "Какая цена на пшеницу 3 класс?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("provider"), "gigachat")
        self.assertTrue(payload.get("text"))

    def test_dry_run_unavailable_when_gigachat_down(self):
        llm_service.gigachat_client.chat_completion = AsyncMock(side_effect=RuntimeError("network down"))
        response = self.client.post("/api/chat/dry-run", json={"text": "Какая цена на ячмень?"})
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertIn("detail", payload)

    def test_llm_status_has_router_fields(self):
        response = self.client.get("/api/llm/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("preferred_provider", payload)
        self.assertIn("fallback_provider", payload)
        self.assertIn("gigachat_enabled", payload)


if __name__ == "__main__":
    unittest.main()
