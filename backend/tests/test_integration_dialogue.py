import os
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_integration_dialogue.db")
os.environ.setdefault("TOXIC_STRICT_MODE", "1")
os.environ.setdefault("LLM_PROVIDER", "gigachat")
os.environ.setdefault("GIGACHAT_AUTH_KEY", "test-auth-key")
os.environ.setdefault("LLM_TEMPLATE_FALLBACK_ENABLED", "1")

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.main import app, llm_service


class IntegrationDialogueCases(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self._orig_chat_completion = llm_service.gigachat_client.chat_completion
        llm_service.gigachat_client.chat_completion = AsyncMock(
            return_value=(
                "Заявку принял. Передаю менеджеру для фиксации цены и логистики.",
                "GigaChat-2",
            )
        )

    def tearDown(self) -> None:
        llm_service.gigachat_client.chat_completion = self._orig_chat_completion

    def _admin_token(self) -> str:
        login = self.client.post("/api/admin/login", json={"username": "admin", "password": os.getenv("ADMIN_PASS", "315920")})
        self.assertEqual(login.status_code, 200)
        return login.json()["token"]

    def test_lead_qualification_flow_and_store(self):
        first = self.client.post(
            "/api/chat",
            json={
                "text": "Интересует пшеница 3 класс",
                "client_id": "integration-lead",
                "source_channel": "telegram",
            },
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()
        self.assertEqual(first_payload.get("provider"), "state-machine")
        self.assertEqual(first_payload.get("state"), "qualification")

        session_id = first_payload["session_id"]

        second = self.client.post(
            "/api/chat",
            json={
                "text": "Объем 300 тонн, доставка в Краснодарский край, отгрузка завтра",
                "session_id": session_id,
                "client_id": "integration-lead",
                "source_channel": "telegram",
            },
        )
        self.assertEqual(second.status_code, 200)
        second_payload = second.json()
        self.assertEqual(second_payload.get("provider"), "state-machine")
        self.assertEqual(second_payload.get("state"), "qualification")

        third = self.client.post(
            "/api/chat",
            json={
                "text": "Контакт +7 900 111 22 33",
                "session_id": session_id,
                "client_id": "integration-lead",
                "source_channel": "telegram",
            },
        )
        self.assertEqual(third.status_code, 200)
        third_payload = third.json()
        self.assertEqual(third_payload.get("state"), "handoff")
        self.assertIn(third_payload.get("provider"), {"gigachat", "template", "state-machine"})

        token = self._admin_token()
        leads_response = self.client.get("/api/admin/leads?limit=200", headers={"x-admin-token": token})
        self.assertEqual(leads_response.status_code, 200)
        leads = leads_response.json()

        matched = [lead for lead in leads if lead.get("session_id") == session_id]
        self.assertTrue(matched)
        latest = matched[0]
        self.assertEqual(latest.get("status"), "qualified")
        self.assertEqual(latest.get("source_channel"), "telegram")
        self.assertTrue(latest.get("raw_dialogue"))

    def test_toxic_message_blocked(self):
        response = self.client.post("/api/chat", json={"text": "иди на хуй", "client_id": "integration-toxic"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("provider"), "guardrails")
        self.assertEqual(payload.get("state"), "stopped_toxic")

    def test_api_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("status"), "ok")
        self.assertEqual(payload.get("agent_engine"), "single-agent-orchestrator")


if __name__ == "__main__":
    unittest.main()
