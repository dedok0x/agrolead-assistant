import json
import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_chat_stream.db")
os.environ.setdefault("OLLAMA_BASE", "http://127.0.0.1:11434")
os.environ.setdefault("NANOCLAW_BASE_URL", "http://127.0.0.1:8788")

from fastapi.testclient import TestClient

from app.main import app


class ChatStreamCases(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_chat_stream_returns_done(self):
        resp = self.client.post(
            "/api/chat/stream",
            json={"text": "Привет", "client_id": "test-stream"},
            headers={"accept": "application/x-ndjson"},
        )
        self.assertEqual(resp.status_code, 200)
        lines = [x for x in resp.text.splitlines() if x.strip()]
        self.assertTrue(lines)
        payload = json.loads(lines[-1])
        self.assertTrue(payload.get("done"))

    def test_toxic_message_hard_stop(self):
        resp = self.client.post("/api/chat", json={"text": "иди нахуй", "client_id": "test-toxic"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("provider"), "guardrails")
        self.assertTrue(data.get("done"))

    def test_dry_run_shape(self):
        resp = self.client.post("/api/chat/dry-run", json={"text": "Нужна пшеница 3 класс"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("provider", data)
        self.assertIn("text", data)

    def test_llm_status_has_real_provider_fields(self):
        resp = self.client.get("/api/llm/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("last_provider", data)
        self.assertIn("last_model", data)

    def test_nanoclaw_adapter_shape(self):
        resp = self.client.post(
            "/api/nanoclaw/agent/chat",
            json={"text": "Нужен прайс по пшенице", "context": "smoke"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("done"))
        self.assertIn("provider", data)


if __name__ == "__main__":
    unittest.main()

