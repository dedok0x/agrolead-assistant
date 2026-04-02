import json
import os
import unittest


os.environ.setdefault("DATABASE_URL", "sqlite:///./test_chat_stream.db")
os.environ.setdefault("OLLAMA_BASE", "http://127.0.0.1:11434")

from fastapi.testclient import TestClient

from app.main import app


class ChatStreamCases(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_chat_stream_blocked_text_returns_done_without_500(self):
        resp = self.client.post(
            "/api/chat/stream",
            json={"text": "123", "client_id": "test-blocked"},
            headers={"accept": "application/x-ndjson"},
        )
        self.assertEqual(resp.status_code, 200)

        lines = [x for x in resp.text.splitlines() if x.strip()]
        self.assertTrue(lines)
        payload = json.loads(lines[-1])

        self.assertTrue(payload.get("done"))
        self.assertIn("session_id", payload)
        self.assertIn("token", payload)

    def test_chat_stream_fast_reply_returns_done_without_500(self):
        resp = self.client.post(
            "/api/chat/stream",
            json={"text": "привет", "client_id": "test-fast"},
            headers={"accept": "application/x-ndjson"},
        )
        self.assertEqual(resp.status_code, 200)

        lines = [x for x in resp.text.splitlines() if x.strip()]
        self.assertTrue(lines)
        payload = json.loads(lines[-1])

        self.assertTrue(payload.get("done"))
        self.assertIn("session_id", payload)
        self.assertTrue(payload.get("token"))

    def test_chat_sync_sales_script_price_question(self):
        resp = self.client.post(
            "/api/chat",
            json={"text": "Какая цена и минимальный объем?", "client_id": "test-price"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("done"))
        self.assertIn("text", data)
        self.assertNotEqual(data.get("provider"), "fallback")

    def test_chat_sync_sales_script_rejects_numeric_noise_without_error(self):
        resp = self.client.post(
            "/api/chat",
            json={"text": "123", "client_id": "test-noise"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("done"))
        self.assertIn("text", data)
        self.assertNotIn("Ошибка запроса", data.get("text", ""))

    def test_chat_dry_run_returns_expected_shape(self):
        resp = self.client.post(
            "/api/chat/dry-run",
            json={"text": "Интересует пшеница 3 класс, объем 100 тонн, доставка в Краснодар"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("done"))
        self.assertIn("provider", data)
        self.assertIn("text", data)

    def test_llm_status_endpoint_shape(self):
        resp = self.client.get("/api/llm/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("active", data)
        self.assertIn("mode", data)

    def test_picoclaw_agent_endpoint_shape(self):
        resp = self.client.post(
            "/api/picoclaw/agent/chat",
            json={"text": "Нужен прайс по пшенице", "context": "smoke"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("done"))
        self.assertIn("provider", data)
        self.assertIn("text", data)


if __name__ == "__main__":
    unittest.main()
