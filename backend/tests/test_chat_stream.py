import json
import os
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_chat_stream.db")
os.environ.setdefault("TOXIC_STRICT_MODE", "1")
os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("OLLAMA_FALLBACK_ENABLED", "1")

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.main import app, llm_service, nanoclaw


class ChatStreamCases(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

        self._orig_llm_complete = llm_service.complete
        self._orig_nanoclaw_chat = nanoclaw.chat

        llm_service.complete = AsyncMock(return_value=("Прайс актуальный, передаю менеджеру.", "ollama", "tinyllama"))
        nanoclaw.chat = AsyncMock(
            return_value={
                "text": "Собрал параметры, передаю менеджеру на закрепление условий.",
                "provider": "ollama",
                "model": "tinyllama",
            }
        )

    def tearDown(self) -> None:
        llm_service.complete = self._orig_llm_complete
        nanoclaw.chat = self._orig_nanoclaw_chat

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

    def test_toxic_message_hard_stop(self):
        response = self.client.post("/api/chat", json={"text": "иди на хуй", "client_id": "test-toxic"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("provider"), "guardrails")
        self.assertEqual(payload.get("state"), "stopped_toxic")

    def test_dry_run_uses_llm(self):
        response = self.client.post("/api/chat/dry-run", json={"text": "Нужна пшеница 3 класс"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("provider"), "ollama")
        self.assertIn("text", payload)

    def test_llm_status_has_provider_fields(self):
        response = self.client.get("/api/llm/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("preferred_provider", payload)
        self.assertIn("last_provider", payload)
        self.assertIn("last_model", payload)

    def test_nanoclaw_adapter_shape(self):
        response = self.client.post(
            "/api/nanoclaw/agent/chat",
            json={"text": "Нужен прайс по пшенице", "context": {"source": "smoke"}},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("done"))
        self.assertEqual(payload.get("provider"), "ollama")


if __name__ == "__main__":
    unittest.main()
