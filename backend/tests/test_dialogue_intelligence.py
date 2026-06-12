import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_dialogue_intelligence_v7.db").resolve()
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


class DialogueIntelligenceCases(unittest.TestCase):
    """Состояние диалога: накопление фактов, подтверждения, отсутствие повторов."""

    def setUp(self):
        startup()
        self.client = TestClient(app)
        self._auth_key = llm_service.gigachat_client.auth_key
        llm_service.gigachat_client.auth_key = ""

    def tearDown(self):
        llm_service.gigachat_client.auth_key = self._auth_key

    def _send(self, text, session_id=None, client_id="intel"):
        response = self.client.post(
            "/api/chat",
            json={"text": text, "client_id": client_id, "session_id": session_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_new_session_created_on_first_message(self):
        payload = self._send("Хочу купить пшеницу", client_id="intel-new")
        self.assertTrue(payload["session_id"])

    def test_session_continues_and_facts_accumulate(self):
        p = self._send("Хочу купить пшеницу", client_id="intel-acc")
        session_id = p["session_id"]
        self.assertEqual(p["known_facts"].get("request_type"), "buy_grain")
        self.assertEqual(p["known_facts"].get("product"), "пшеница")

        p = self._send("200 тонн", session_id, client_id="intel-acc")
        self.assertEqual(p["session_id"], session_id)
        self.assertEqual(p["known_facts"].get("volume"), "200 тонн")
        # ранее собранные факты не потеряны
        self.assertEqual(p["known_facts"].get("request_type"), "buy_grain")
        self.assertEqual(p["known_facts"].get("product"), "пшеница")

    def test_known_fact_is_not_asked_again(self):
        p = self._send("Продажа кукурузы 500 тонн", client_id="intel-norepeat")
        session_id = p["session_id"]
        self.assertEqual(p["known_facts"].get("product"), "кукуруза")
        p = self._send("Краснодарский край", session_id, client_id="intel-norepeat")
        lowered = p["text"].lower()
        self.assertNotIn("какую культуру", lowered)
        self.assertNotIn("какой объем", lowered)
        self.assertNotIn("product", p["missing_fields"])
        self.assertNotIn("volume", p["missing_fields"])

    def test_uncertain_volume_confirmed_with_yes(self):
        p = self._send("Продажа", client_id="intel-confirm")
        session_id = p["session_id"]
        self._send("пшеница", session_id, client_id="intel-confirm")
        p = self._send("3000", session_id, client_id="intel-confirm")
        self.assertNotIn("volume", p["known_facts"])
        self.assertEqual(p["uncertain_facts"].get("volume", {}).get("normalized_value"), "3000 тонн")
        self.assertIn("volume", p["missing_fields"])

        p = self._send("да", session_id, client_id="intel-confirm")
        self.assertEqual(p["known_facts"].get("volume"), "3000 тонн")
        self.assertNotIn("volume", p["missing_fields"])
        self.assertNotIn("volume", p["uncertain_facts"])

    def test_uncertain_volume_rejected_with_no(self):
        p = self._send("Продажа пшеницы", client_id="intel-reject")
        session_id = p["session_id"]
        p = self._send("3000", session_id, client_id="intel-reject")
        self.assertIn("volume", p["uncertain_facts"])

        p = self._send("нет", session_id, client_id="intel-reject")
        self.assertNotIn("volume", p["known_facts"])
        self.assertNotIn("volume", p["uncertain_facts"])
        self.assertIn("volume", p["missing_fields"])
        # бот заново спрашивает объем
        self.assertIn("объем", p["text"].lower())

    def test_uncertain_fact_does_not_close_missing_field(self):
        p = self._send("Продажа пшеницы", client_id="intel-uncertain")
        session_id = p["session_id"]
        p = self._send("3000", session_id, client_id="intel-uncertain")
        self.assertIn("volume", p["missing_fields"])
        self.assertLess(p["qualification_score"], 100)
        # uncertain переживает реплику: следующее сообщение не про объем
        p = self._send("ага", session_id, client_id="intel-uncertain")
        self.assertEqual(p["known_facts"].get("volume"), "3000 тонн")

    def test_weak_marker_does_not_override_request_type(self):
        p = self._send("Продажа пшеницы 100 тонн", client_id="intel-override")
        session_id = p["session_id"]
        self.assertEqual(p["known_facts"].get("request_type"), "sell_grain")
        p = self._send("нужно сначала уточнить у бухгалтера", session_id, client_id="intel-override")
        self.assertEqual(p["known_facts"].get("request_type"), "sell_grain")

    def test_explicit_switch_overrides_request_type(self):
        p = self._send("Продажа пшеницы", client_id="intel-switch")
        session_id = p["session_id"]
        p = self._send("нет, наоборот, хотим купить ячмень", session_id, client_id="intel-switch")
        self.assertEqual(p["known_facts"].get("request_type"), "buy_grain")

    def test_qualification_score_grows_with_facts(self):
        scores = []
        session_id = None
        for text in ["Продажа", "пшеница", "400 тонн", "Новороссийск", "срочно", "+79001234567"]:
            p = self._send(text, session_id, client_id="intel-score")
            session_id = p["session_id"]
            scores.append(p["qualification_score"])
        self.assertEqual(scores, sorted(scores))
        self.assertEqual(scores[-1], 100)
        self.assertEqual(p["status"], "qualified")
        self.assertEqual(p["next_action"], "handoff_manager")

    def test_empty_input_returns_400(self):
        response = self.client.post("/api/chat", json={"text": "   ", "client_id": "intel-empty"})
        self.assertEqual(response.status_code, 400)

    def test_buy_with_delivery_word_is_not_logistics(self):
        p = self._send(
            "Здравствуйте, хочу купить 100 тонн пшеницы с доставкой в Краснодар. Телефон +7 900 111-22-33.",
            client_id="intel-delivery",
        )
        self.assertEqual(p["known_facts"].get("request_type"), "buy_grain")
        self.assertEqual(p["known_facts"].get("volume"), "100 тонн")
        self.assertEqual(p["known_facts"].get("region"), "Краснодарский край")
        self.assertIn("contact", p["known_facts"])
        # фрагмент телефона не должен стать сроком
        self.assertNotIn(p["known_facts"].get("timing", ""), {"11-22", "22-33"})


if __name__ == "__main__":
    unittest.main()
