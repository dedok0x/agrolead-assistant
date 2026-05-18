import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_agentic_dialogue_v7.db").resolve()
if DB_FILE.exists():
    DB_FILE.unlink()

os.environ.setdefault("DATABASE_URL", f"sqlite:///{DB_FILE.as_posix()}")
os.environ.setdefault("TOXIC_STRICT_MODE", "1")
os.environ.setdefault("LLM_PROVIDER", "gigachat")
os.environ.setdefault("GIGACHAT_AUTH_KEY", "test-auth-key")
os.environ.setdefault("GIGACHAT_VERIFY_SSL", "0")
os.environ.setdefault("ADMIN_PASS", "test-admin-password")

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.main import app, llm_service, startup


class AgenticDialogueCases(unittest.TestCase):
    def setUp(self):
        startup()
        self.client = TestClient(app)
        self._auth_key = llm_service.gigachat_client.auth_key
        llm_service.gigachat_client.auth_key = ""

    def tearDown(self):
        llm_service.gigachat_client.auth_key = self._auth_key

    def _send(self, text, session_id=None):
        response = self.client.post(
            "/api/chat",
            json={"text": text, "client_id": "agentic-dialogue", "session_id": session_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_meta_consultation_and_feedback_are_not_treated_as_form_fields(self):
        session_id = None
        messages = [
            "\u0422\u044b \u043a\u0442\u043e",
            "\u0443\u0442\u043e\u0447\u043d\u0438",
            "\u043f\u0440\u043e\u0441\u0442\u043e",
            "\u0433\u043e\u0432\u043e\u0440\u044e \u043f\u0440\u043e\u0441\u0442\u043e \u043f\u0440\u043e\u043a\u043e\u043d\u0441\u0443\u043b\u044c\u0442\u0438\u0440\u0443\u0439 \u043a\u0442\u043e \u0432\u044b",
            "\u043d\u0443 \u0447\u0442\u043e \u0442\u043e \u043f\u043e\u043a\u0430 \u0447\u0442\u043e \u0438\u043d\u0442\u0435\u043b\u043b\u0435\u043a\u0442\u043e\u043c \u0442\u044b \u043d\u0435 \u0431\u043b\u0435\u0449\u0435\u0448\u044c",
        ]
        responses = []
        for text in messages:
            payload = self._send(text, session_id)
            session_id = payload["session_id"]
            responses.append(payload)

        self.assertIn("\u0430\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442", responses[0]["text"].lower())
        self.assertNotIn("product", responses[2]["known_facts"])
        self.assertNotEqual(responses[2]["known_facts"].get("product"), "\u043f\u0440\u043e\u0441\u043e")
        self.assertEqual(responses[3]["known_facts"].get("request_type"), "consultation")
        self.assertNotIn("product", responses[3]["known_facts"])
        self.assertNotIn("volume", responses[3]["known_facts"])
        self.assertNotIn("\u043a\u0430\u043a\u043e\u0439 \u043e\u0431\u044a\u0435\u043c", responses[4]["text"].lower())
        self.assertIn("\u0441\u043b\u0438\u0448\u043a\u043e\u043c", responses[4]["text"].lower())

    def test_smalltalk_does_not_turn_into_millet_or_form_loop(self):
        session_id = None
        messages = [
            "\u041f\u0440\u0438\u0432\u0435\u0442",
            "\u0427\u0442\u043e",
            "\u0430, \u0434\u0430 \u044f \u043f\u0440\u043e\u0441\u0442\u043e \u043f\u043e\u0431\u043e\u043b\u0442\u0430\u0442\u044c",
            "\u0434\u0430 \u043d\u0435 \u043f\u0440\u043e\u0441\u043e",
            "\u043d\u0443 \u0435\u043c\u0430\u0435",
            "\u0433\u0434\u0435 \u0433\u0438\u0433\u0430\u0447\u0430\u0442",
            "\u043e\u043f\u044f\u0442\u044c 25",
        ]
        responses = []
        for text in messages:
            payload = self._send(text, session_id)
            session_id = payload["session_id"]
            responses.append(payload)

        for payload in responses:
            self.assertNotEqual(payload["known_facts"].get("product"), "\u043f\u0440\u043e\u0441\u043e")
            self.assertNotIn("\u043a\u0430\u043a\u0443\u044e \u043a\u0443\u043b\u044c\u0442\u0443\u0440\u0443", payload["text"].lower())
            self.assertNotIn("\u043a\u0430\u043a\u043e\u0439 \u043e\u0431\u044a\u0435\u043c", payload["text"].lower())
        self.assertNotIn("product", responses[-1]["known_facts"])


if __name__ == "__main__":
    unittest.main()
