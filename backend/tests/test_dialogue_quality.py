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
from tests._agent_mock import install_fake_gigachat


class DialogueQualityCases(unittest.TestCase):
    def setUp(self):
        startup()
        self.client = TestClient(app)
        self._auth_key = llm_service.gigachat_client.auth_key
        install_fake_gigachat(llm_service)

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
        # GigaChat-driven: проверяем извлечение фактов и отсутствие служебной утечки.
        # Точные шаблонные формулировки старого движка больше не проверяем — реплику
        # формирует модель.
        session_id = None

        p = self._send("чё можешь", session_id)
        session_id = p["session_id"]
        self.assertNotIn("{", p["text"])

        p = self._send("продажа", session_id)
        self.assertEqual(p["known_facts"].get("request_type"), "sell_grain")
        self.assertEqual(p["next_action"], "ask_product")

        p = self._send("Пшениица", session_id)
        self.assertEqual(p["known_facts"].get("product"), "пшеница")
        self.assertEqual(p["next_action"], "ask_volume")

        p = self._send("400 тоннн", session_id)
        self.assertEqual(p["known_facts"].get("volume"), "400 тонн")
        self.assertEqual(p["next_action"], "ask_region")

        p = self._send("новороссийск", session_id)
        self.assertEqual(p["known_facts"].get("region"), "Новороссийск")
        self.assertEqual(p["next_action"], "ask_timing")

        p = self._send("да как удобно", session_id)
        self.assertEqual(p["known_facts"].get("timing"), "по согласованию")
        self.assertEqual(p["next_action"], "ask_contact")

        p = self._send("89186670349", session_id)
        self.assertIn("contact", p["known_facts"])
        self.assertEqual(p["status"], "qualified")
        self.assertEqual(p["qualification_score"], 100)
        self.assertNotIn("{", p["text"])

    def test_ready_for_manager_requires_valid_confirmed_fields(self):
        session_id = None
        for text in ["продажа", "Пшениица", "большой", "3000", "новороссийск", "89186670349"]:
            payload = self._send(text, session_id)
            session_id = payload["session_id"]
            self.assertNotEqual(payload["status"], "qualified")
        self.assertNotIn("volume", payload["known_facts"])


if __name__ == "__main__":
    unittest.main()
