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


if __name__ == "__main__":
    unittest.main()
