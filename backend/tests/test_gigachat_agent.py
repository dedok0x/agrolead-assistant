import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_gigachat_agent.db").resolve()
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
from tests._agent_mock import install_gigachat, turn_json


class GigaChatAgentCases(unittest.TestCase):
    def setUp(self):
        startup()
        self.client = TestClient(app)
        self._auth_key = llm_service.gigachat_client.auth_key

    def tearDown(self):
        llm_service.gigachat_client.auth_key = self._auth_key

    def _post(self, text, **kw):
        body = {"text": text, "source_channel": "web_widget"}
        body.update(kw)
        return self.client.post("/api/chat", json=body)

    def test_ai_extraction_qualifies_lead(self):
        # GigaChat за один ход возвращает полный набор фактов — лид должен закрыться.
        install_gigachat(llm_service, [
            turn_json(
                reply_text="Зафиксировал пшеницу 1000 тонн из Краснодара, передаю менеджеру.",
                request_type="sell_grain",
                updated_facts={
                    "request_type": "sell_grain",
                    "product": "пшеница",
                    "volume": "1000 тонн",
                    "region": "Краснодарский край",
                    "timing": "октябрь",
                    "contact": "+7 900 111-22-33",
                    "quality_class": "3 класс",
                },
                newly_extracted=["product", "volume", "region", "timing", "contact"],
            ),
        ])
        resp = self._post("Продам пшеницу 1000 тонн 3 класс из Краснодара, тел +7 900 111-22-33, октябрь",
                          client_id="agent-qual-1")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertEqual(payload["known_facts"]["product"], "пшеница")
        self.assertEqual(payload["known_facts"]["volume"], "1000 тонн")
        self.assertEqual(payload["known_facts"]["contact"], "+7 900 111-22-33")
        self.assertIsNotNone(payload["lead_id"])
        self.assertEqual(payload["missing_fields"], [])
        self.assertIn(payload["status"], {"qualified", "handed_to_manager"})
        self.assertEqual(payload["provider"], "gigachat")

    def test_service_message_creates_no_lead(self):
        # Приветствие/болтовня помечены is_service_message — заявки быть не должно.
        install_gigachat(llm_service, [
            turn_json(
                reply_text="Здравствуйте! Я ассистент ПЕТРОХЛЕБ-КУБАНЬ, помогу с зерном и логистикой.",
                intent="smalltalk",
                is_service_message=True,
            ),
        ])
        resp = self._post("привет, как дела", client_id="agent-smalltalk-1")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertIsNone(payload["lead_id"])
        self.assertEqual(payload["known_facts"], {})
        self.assertEqual(payload["status"], "faq_only")
        self.assertTrue(payload["text"])

    def test_partial_then_correction(self):
        # Первый ход: просо. Второй ход: клиент поправляет на пшеницу.
        install_gigachat(llm_service, [
            turn_json(
                reply_text="Записал просо. Какой объём в тоннах?",
                request_type="sell_grain",
                updated_facts={"request_type": "sell_grain", "product": "просо"},
                newly_extracted=["product"],
            ),
            turn_json(
                reply_text="Поправил на пшеницу. Какой объём?",
                request_type="sell_grain",
                updated_facts={"request_type": "sell_grain", "product": "пшеница"},
                newly_extracted=["product"],
            ),
        ])
        first = self._post("продам просо", client_id="agent-corr-1")
        self.assertEqual(first.json()["known_facts"]["product"], "просо")
        session_id = first.json()["session_id"]
        second = self._post("нет, не просо, а пшеница", client_id="agent-corr-1", session_id=session_id)
        self.assertEqual(second.json()["known_facts"]["product"], "пшеница")

    def test_fact_removal_on_empty_value(self):
        install_gigachat(llm_service, [
            turn_json(
                reply_text="Записал просо.",
                request_type="sell_grain",
                updated_facts={"request_type": "sell_grain", "product": "просо"},
                newly_extracted=["product"],
            ),
            turn_json(
                reply_text="Убрал культуру, уточните, что фиксируем.",
                request_type="sell_grain",
                updated_facts={"request_type": "sell_grain", "product": ""},
            ),
        ])
        first = self._post("продам просо", client_id="agent-rm-1")
        session_id = first.json()["session_id"]
        second = self._post("забудьте про культуру", client_id="agent-rm-1", session_id=session_id)
        self.assertNotIn("product", second.json()["known_facts"])

    def test_fail_closed_without_gigachat(self):
        # GigaChat не сконфигурирован — fail-closed, без падения и без фактов.
        llm_service.gigachat_client.auth_key = ""
        resp = self._post("продам пшеницу 500 тонн", client_id="agent-failclosed-1")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertEqual(payload["provider"], "fail-closed")
        self.assertTrue(payload["text"])
        self.assertEqual(payload["known_facts"], {})
        self.assertIsNone(payload["lead_id"])


if __name__ == "__main__":
    unittest.main()
