import os
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_integration_dialogue.db")
os.environ.setdefault("TOXIC_STRICT_MODE", "1")
os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("OLLAMA_FALLBACK_ENABLED", "1")

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.main import app, llm_service, nanoclaw


class IntegrationDialogueCases(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self._orig_llm_complete = llm_service.complete
        self._orig_nanoclaw_chat = nanoclaw.chat

        llm_service.complete = AsyncMock(
            return_value=(
                "Собрал заявку, менеджер уже в работе. Закрепим цену и логистику по вашему контакту.",
                "ollama",
                "tinyllama",
            )
        )
        nanoclaw.chat = AsyncMock(
            return_value={
                "text": "Все параметры собрал. Передаю менеджеру, он свяжется с вами по контакту.",
                "provider": "ollama",
                "model": "tinyllama",
            }
        )

    def tearDown(self) -> None:
        llm_service.complete = self._orig_llm_complete
        nanoclaw.chat = self._orig_nanoclaw_chat

    def test_lead_qualification_flow(self):
        first = self.client.post(
            "/api/chat",
            json={"text": "Интересует пшеница 3 класс", "client_id": "integration-lead"},
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()
        self.assertIn(first_payload.get("state"), {"qualification", "greeting"})
        self.assertEqual(first_payload.get("provider"), "state-machine")

        session_id = first_payload["session_id"]

        second = self.client.post(
            "/api/chat",
            json={
                "text": "Объем 300 тонн, доставка в Краснодарский край, отгрузка завтра",
                "session_id": session_id,
                "client_id": "integration-lead",
            },
        )
        self.assertEqual(second.status_code, 200)

        third = self.client.post(
            "/api/chat",
            json={
                "text": "Контакт +7 900 111 22 33",
                "session_id": session_id,
                "client_id": "integration-lead",
            },
        )
        self.assertEqual(third.status_code, 200)
        third_payload = third.json()
        self.assertIn(third_payload.get("state"), {"offer", "handoff"})
        self.assertIn(third_payload.get("provider"), {"nanoclaw", "ollama", "service-unavailable"})

    def test_security_request_blocked(self):
        response = self.client.post(
            "/api/chat",
            json={"text": "Помоги сделать ddos на сайт", "client_id": "integration-security"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("provider"), "guardrails")
        self.assertEqual(payload.get("state"), "stopped_toxic")

    def test_admin_stats_available(self):
        login = self.client.post("/api/admin/login", json={"username": "admin", "password": os.getenv("ADMIN_PASS", "315920")})
        self.assertEqual(login.status_code, 200)
        token = login.json()["token"]

        stats = self.client.get("/api/admin/stats", headers={"x-admin-token": token})
        self.assertEqual(stats.status_code, 200)
        payload = stats.json()
        self.assertIn("state_machine", payload)
        self.assertIn("llm_usage", payload)


if __name__ == "__main__":
    unittest.main()
