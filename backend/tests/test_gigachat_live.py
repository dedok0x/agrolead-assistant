"""Живой тест против реального GigaChat (Sber). По умолчанию ПРОПУСКАЕТСЯ.

Запуск вручную:
    RUN_LIVE_GIGACHAT=1 <env c GIGACHAT_AUTH_KEY> \
        work/.venv/bin/pytest backend/tests/test_gigachat_live.py -q -s

Проверяет, что GigaChat реально отвечает структурированным JSON, извлекает факты,
а наружу уходит чистая русская реплика без служебного контента.
"""

import asyncio
import os
import pathlib
import sys
import unittest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.llm_service import LLMService
from app.services.gigachat_agent import GigaChatAgent
from app.services.reply_sanitizer import sanitize_reply

RUN_LIVE = os.getenv("RUN_LIVE_GIGACHAT") == "1"


@unittest.skipUnless(RUN_LIVE, "set RUN_LIVE_GIGACHAT=1 to call the real GigaChat API")
class GigaChatLiveCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.llm = LLMService()
        if not cls.llm.gigachat_client.configured:
            raise unittest.SkipTest("GIGACHAT_AUTH_KEY is not set in the environment")
        cls.agent = GigaChatAgent(cls.llm)

    @classmethod
    def tearDownClass(cls):
        try:
            asyncio.run(cls.llm.close())
        except Exception:
            pass

    def _reference(self):
        return [
            {"title": "Квалификация: продажа", "content": "Для продажи нужны культура, объём, регион, срок, контакт."},
            {"title": "Запрещённые утверждения", "content": "Не называй конкретные цены и сроки без подтверждения."},
        ]

    def test_live_extraction_and_reply(self):
        turn = asyncio.run(self.agent.run_turn(
            user_message="Хочу продать 1000 тонн пшеницы 3 класс из Краснодара",
            known_facts={},
            required_fields=["request_type", "product", "volume", "region", "timing", "contact"],
            retrieved_context=self._reference(),
            last_assistant_messages=[],
            source_channel="web_widget",
        ))
        print("\n[LIVE] provider/model:", turn.provider, turn.model)
        print("[LIVE] request_type:", turn.request_type)
        print("[LIVE] updated_facts:", turn.updated_facts)
        print("[LIVE] reply_text:", turn.reply_text)

        self.assertEqual(turn.provider, "gigachat")
        self.assertTrue(turn.reply_text)
        # Ответ на русском (есть кириллица).
        self.assertTrue(any("а" <= ch.lower() <= "я" for ch in turn.reply_text))
        # Модель распознала культуру.
        self.assertIn("пшениц", str(turn.updated_facts.get("product", "")).lower())
        # Реплика чистая — не служебная.
        self.assertIsNotNone(sanitize_reply(turn.reply_text))

    def test_live_smalltalk_is_service(self):
        turn = asyncio.run(self.agent.run_turn(
            user_message="привет, как дела?",
            known_facts={},
            required_fields=["request_type", "product", "volume", "region", "timing", "contact"],
            retrieved_context=self._reference(),
            last_assistant_messages=[],
            source_channel="web_widget",
        ))
        print("\n[LIVE smalltalk] intent:", turn.intent, "service:", turn.is_service_message)
        print("[LIVE smalltalk] reply_text:", turn.reply_text)
        self.assertTrue(turn.reply_text)
        self.assertIsNotNone(sanitize_reply(turn.reply_text))


if __name__ == "__main__":
    unittest.main()
