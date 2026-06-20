import os
import pathlib
import sys
import unittest

DB_FILE = pathlib.Path("./test_no_service_leak.db").resolve()
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
from app.services.reply_sanitizer import SERVICE_TOKENS, sanitize_reply
from tests._agent_mock import install_gigachat, install_gigachat_raw, turn_json


def _is_clean(text: str) -> bool:
    lowered = (text or "").lower()
    if "{" in text or "}" in text:
        return False
    return not any(token in lowered for token in SERVICE_TOKENS)


class ReplySanitizerUnitCases(unittest.TestCase):
    def test_rejects_json_and_codes(self):
        self.assertIsNone(sanitize_reply('{"reply_text": "привет"}'))
        self.assertIsNone(sanitize_reply("next_action: ask_volume"))
        self.assertIsNone(sanitize_reply("missing_fields: [contact]"))
        self.assertIsNone(sanitize_reply(""))

    def test_keeps_clean_text(self):
        self.assertEqual(sanitize_reply("Здравствуйте! Чем помочь по зерну?"),
                         "Здравствуйте! Чем помочь по зерну?")

    def test_strips_code_fences(self):
        self.assertEqual(sanitize_reply("```\nПривет\n```"), "Привет")


class NoServiceLeakInChatCases(unittest.TestCase):
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

    def test_leaked_reply_is_replaced(self):
        # Модель «протекла» служебным JSON в reply_text — наружу это уйти не должно.
        install_gigachat_raw(llm_service, [
            '{"intent": "commercial", "is_service_message": false, "request_type": "sell_grain", '
            '"updated_facts": {"request_type": "sell_grain", "product": "пшеница"}, '
            '"newly_extracted": ["product"], '
            '"reply_text": "{\\"next_action\\": \\"ask_volume\\", \\"missing_fields\\": [\\"volume\\"]}"}'
        ])
        resp = self._post("продам пшеницу", client_id="leak-1")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertTrue(_is_clean(payload["text"]), payload["text"])
        self.assertEqual(payload["provider"], "sanitizer")
        # Факт при этом извлёкся нормально.
        self.assertEqual(payload["known_facts"]["product"], "пшеница")

    def test_battery_of_messages_stay_clean(self):
        responses = [
            turn_json(reply_text="Здравствуйте! Помогу с зерном, логистикой и ВЭД.",
                      intent="smalltalk", is_service_message=True),
            turn_json(reply_text="Мы закупаем и продаём зерно, возим и храним. Что вас интересует?",
                      intent="faq", is_service_message=True),
            turn_json(reply_text="Записал ячмень. Какой объём в тоннах?",
                      request_type="sell_grain",
                      updated_facts={"request_type": "sell_grain", "product": "ячмень"},
                      newly_extracted=["product"]),
            turn_json(reply_text="Это вне нашего профиля, но помогу по зерну и логистике.",
                      intent="irrelevant", is_service_message=True),
        ]
        install_gigachat(llm_service, responses)
        messages = ["привет", "чем вы занимаетесь", "продам ячмень", "расскажи анекдот"]
        for idx, msg in enumerate(messages):
            resp = self._post(msg, client_id="leak-battery", session_id=f"leak-battery-{idx}")
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(_is_clean(resp.json()["text"]), resp.json()["text"])


if __name__ == "__main__":
    unittest.main()
