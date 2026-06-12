import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_sales_assistant_flow_v7.db").resolve()
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


class SalesAssistantFlowCases(unittest.TestCase):
    """Полный путь покупателя: от неполной заявки до передачи менеджеру."""

    def setUp(self):
        startup()
        self.client = TestClient(app)
        self._auth_key = llm_service.gigachat_client.auth_key
        llm_service.gigachat_client.auth_key = ""

    def tearDown(self):
        llm_service.gigachat_client.auth_key = self._auth_key

    def _send(self, text, session_id=None, client_id="flow"):
        response = self.client.post(
            "/api/chat",
            json={"text": text, "client_id": client_id, "session_id": session_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_incomplete_buy_request_completed_step_by_step(self):
        session_id = None
        asked_questions = []

        p = self._send("Хочу купить пшеницу.", client_id="flow-steps")
        session_id = p["session_id"]
        self.assertEqual(p["known_facts"]["request_type"], "buy_grain")
        self.assertEqual(p["known_facts"]["product"], "пшеница")
        self.assertEqual(p["next_action"], "ask_volume")
        asked_questions.append(p["next_action"])

        p = self._send("200 тонн", session_id, client_id="flow-steps")
        self.assertEqual(p["known_facts"]["volume"], "200 тонн")
        self.assertEqual(p["next_action"], "ask_region")
        asked_questions.append(p["next_action"])

        p = self._send("в Краснодар", session_id, client_id="flow-steps")
        self.assertEqual(p["known_facts"]["region"], "Краснодарский край")
        self.assertEqual(p["next_action"], "ask_timing")
        asked_questions.append(p["next_action"])

        p = self._send("в июне", session_id, client_id="flow-steps")
        self.assertIn("июн", p["known_facts"]["timing"])
        self.assertEqual(p["next_action"], "ask_contact")
        asked_questions.append(p["next_action"])

        p = self._send("+7 900 111-22-33", session_id, client_id="flow-steps")
        self.assertIn("contact", p["known_facts"])
        self.assertEqual(p["next_action"], "handoff_manager")
        self.assertEqual(p["status"], "qualified")
        self.assertEqual(p["qualification_score"], 100)
        self.assertEqual(p["missing_fields"], [])

        # вопросы не повторялись: каждый ask_* встречался один раз
        self.assertEqual(len(asked_questions), len(set(asked_questions)))

    def test_handoff_summary_mentions_collected_facts(self):
        session_id = None
        for text in ["Продажа ячменя", "300 тонн", "Ростовская область", "срочно"]:
            p = self._send(text, session_id, client_id="flow-handoff")
            session_id = p["session_id"]
        p = self._send("+79281234567", session_id, client_id="flow-handoff")
        self.assertEqual(p["status"], "qualified")
        lowered = p["text"].lower()
        self.assertTrue("заявка" in lowered or "менеджер" in lowered, p["text"])

    def test_session_keeps_state_after_meta_questions(self):
        p = self._send("Продажа пшеницы 100 тонн", client_id="flow-meta")
        session_id = p["session_id"]
        p = self._send("а ты кто вообще?", session_id, client_id="flow-meta")
        # мета-вопрос не стирает собранное состояние
        self.assertEqual(p["known_facts"].get("request_type"), "sell_grain")
        self.assertEqual(p["known_facts"].get("product"), "пшеница")
        p = self._send("Новороссийск", session_id, client_id="flow-meta")
        self.assertEqual(p["known_facts"].get("region"), "Новороссийск")

    def test_consultation_switches_to_lead_on_intent(self):
        p = self._send("просто проконсультируйте", client_id="flow-consult")
        session_id = p["session_id"]
        self.assertIsNone(p["lead_id"])
        p = self._send("хочу продать 500 тонн кукурузы из Краснодара", session_id, client_id="flow-consult")
        self.assertTrue(p["lead_id"])
        self.assertEqual(p["known_facts"]["request_type"], "sell_grain")
        self.assertEqual(p["known_facts"]["volume"], "500 тонн")


if __name__ == "__main__":
    unittest.main()
