import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_dialogue_quality_v7.db").resolve()
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


class DialogueQualityCases(unittest.TestCase):
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
            json={"text": text, "client_id": "dialogue-quality", "session_id": session_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_full_bad_dialogue_becomes_assistant_flow(self):
        session_id = None
        seen_texts = []

        p = self._send("чё можешь", session_id)
        session_id = p["session_id"]
        self.assertIn("помочь оформить заявку", p["text"])
        self.assertIn("продажи", p["text"])
        seen_texts.append(p["text"])

        p = self._send("что", session_id)
        self.assertIn("Проще говоря", p["text"])
        seen_texts.append(p["text"])

        p = self._send("продажа", session_id)
        self.assertEqual(p["known_facts"].get("request_type"), "sell_grain")
        self.assertEqual(p["next_action"], "ask_product")
        seen_texts.append(p["text"])

        p = self._send("греча", session_id)
        self.assertNotIn("product", p["known_facts"])
        self.assertIn("product", p["uncertain_facts"])
        self.assertIn("гречиху", p["text"].lower())
        seen_texts.append(p["text"])

        p = self._send("гречка", session_id)
        self.assertNotIn("product", p["known_facts"])
        self.assertIn("гречиху", p["text"].lower())
        seen_texts.append(p["text"])

        p = self._send("Пшениица", session_id)
        self.assertEqual(p["known_facts"].get("product"), "пшеница")
        self.assertEqual(p["next_action"], "ask_volume")
        self.assertIn("объем", p["text"].lower())
        seen_texts.append(p["text"])

        p = self._send("пшено", session_id)
        self.assertEqual(p["known_facts"].get("product"), "пшено")
        self.assertNotEqual(p["known_facts"].get("region"), "пшено")
        seen_texts.append(p["text"])

        p = self._send("большой", session_id)
        self.assertNotIn("volume", p["known_facts"])
        self.assertIn("диапазон", p["text"].lower())
        seen_texts.append(p["text"])

        p = self._send("Та пипейц", session_id)
        self.assertNotIn("volume", p["known_facts"])
        self.assertIn("давайте проще", p["text"].lower())
        seen_texts.append(p["text"])

        p = self._send("да никакой", session_id)
        self.assertNotIn("volume", p["known_facts"])
        self.assertIn("не буду фиксировать объем", p["text"].lower())
        seen_texts.append(p["text"])

        p = self._send("3000", session_id)
        self.assertNotIn("volume", p["known_facts"])
        self.assertEqual(p["uncertain_facts"].get("volume", {}).get("normalized_value"), "3000 тонн")
        self.assertIn("правильно понял", p["text"].lower())
        seen_texts.append(p["text"])

        p = self._send("400 тоннн", session_id)
        self.assertEqual(p["known_facts"].get("volume"), "400 тонн")
        self.assertEqual(p["next_action"], "ask_region")
        seen_texts.append(p["text"])

        p = self._send("40 тон", session_id)
        self.assertEqual(p["known_facts"].get("volume"), "40 тонн")
        self.assertNotEqual(p["known_facts"].get("region"), "40 тон")
        seen_texts.append(p["text"])

        p = self._send("40 тонн", session_id)
        self.assertEqual(p["known_facts"].get("volume"), "40 тонн")
        seen_texts.append(p["text"])

        p = self._send("новороссийск", session_id)
        self.assertEqual(p["known_facts"].get("region"), "Новороссийск")
        self.assertEqual(p["next_action"], "ask_timing")
        seen_texts.append(p["text"])

        p = self._send("да как удобно", session_id)
        self.assertEqual(p["known_facts"].get("timing"), "по согласованию")
        self.assertNotEqual(p["known_facts"].get("timing"), "к удобно")
        self.assertEqual(p["next_action"], "ask_contact")
        seen_texts.append(p["text"])

        p = self._send("89186670349", session_id)
        self.assertIn("contact", p["known_facts"])
        self.assertEqual(p["next_action"], "handoff_manager")
        self.assertEqual(p["status"], "qualified")
        self.assertEqual(p["qualification_score"], 100)
        seen_texts.append(p["text"])

        for previous, current in zip(seen_texts, seen_texts[1:]):
            self.assertNotEqual(previous, current)

    def test_ready_for_manager_requires_valid_confirmed_fields(self):
        session_id = None
        for text in ["продажа", "Пшениица", "большой", "3000", "новороссийск", "89186670349"]:
            payload = self._send(text, session_id)
            session_id = payload["session_id"]
            self.assertNotEqual(payload["status"], "qualified")
        self.assertNotIn("volume", payload["known_facts"])


if __name__ == "__main__":
    unittest.main()
