from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from sqlmodel import Session

from ..domain.sales import NEXT_ACTION_LABELS
from .conversation_service import ConversationService

LOGGER = logging.getLogger("agrolead.telegram")


START_TEXT = (
    "Здравствуйте! Я AI-ассистент ПЕТРОХЛЕБ-КУБАНЬ. "
    "Помогу оформить заявку по закупке, продаже зерна, логистике, хранению или ВЭД."
)

HELP_TEXT = (
    "Например: Продажа пшеницы 3 класс, 400 тонн, Краснодарский край, контакт +7..."
)


CALLBACK_TEXT = {
    "sell_grain": "Продажа зерна",
    "buy_grain": "Купить зерно",
    "logistics": "Логистика",
    "storage": "Хранение",
    "export": "ВЭД",
    "consultation": "Консультация",
    "manager": "Связаться с менеджером",
}


class TelegramService:
    def __init__(self, conversation_service: ConversationService) -> None:
        self.conversation_service = conversation_service
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.webhook_url = os.getenv(
            "TELEGRAM_WEBHOOK_URL",
            "https://artemshtodin.ru/api/integrations/telegram/webhook",
        ).strip()
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def handle_update(self, update: dict[str, Any], *, db_session: Session) -> dict[str, Any]:
        message = update.get("message") or {}
        callback = update.get("callback_query") or {}
        if callback:
            message = callback.get("message") or {}
            data = str(callback.get("data") or "")
            text = CALLBACK_TEXT.get(data, data)
            await self._answer_callback(callback.get("id"))
        else:
            text = str(message.get("text") or "").strip()

        chat = message.get("chat") or {}
        user = (callback.get("from") if callback else message.get("from")) or {}
        chat_id = chat.get("id")
        user_id = user.get("id") or chat_id
        if not chat_id or not user_id:
            return {"ok": True, "enabled": self.enabled, "handled": False, "detail": "unsupported update"}

        external_user_id = str(user_id)
        if not text:
            await self._send_message(chat_id, "Пришлите текст заявки или выберите сценарий.", reply_markup=self._keyboard())
            return {"ok": True, "enabled": self.enabled, "handled": True, "detail": "empty text"}

        if text.startswith("/start"):
            await self._send_message(chat_id, START_TEXT, reply_markup=self._keyboard())
            return {"ok": True, "enabled": self.enabled, "handled": True, "detail": "start"}
        if text.startswith("/help"):
            await self._send_message(chat_id, HELP_TEXT, reply_markup=self._keyboard())
            return {"ok": True, "enabled": self.enabled, "handled": True, "detail": "help"}
        if text.startswith("/new"):
            result = await self.conversation_service.handle_message(
                "Новая заявка",
                client_id=f"telegram:{external_user_id}",
                source_channel="telegram",
                external_user_id=external_user_id,
                metadata={"force_new_session": True},
                db_session=db_session,
            )
            await self._send_message(chat_id, f"Начали новую заявку.\n{result.text}", reply_markup=self._keyboard())
            return {"ok": True, "enabled": self.enabled, "handled": True, "detail": "new"}
        if text.startswith("/status"):
            status = self.conversation_service.get_status(
                db_session=db_session,
                external_user_id=external_user_id,
                source_channel="telegram",
            )
            await self._send_message(chat_id, self._render_status(status), reply_markup=self._keyboard())
            return {"ok": True, "enabled": self.enabled, "handled": True, "detail": "status"}
        if text.startswith("/contact"):
            await self._send_message(chat_id, "Оставьте телефон, Telegram или email, и менеджер сможет связаться с вами.", reply_markup=self._keyboard())
            return {"ok": True, "enabled": self.enabled, "handled": True, "detail": "contact"}

        result = await self.conversation_service.handle_message(
            text,
            client_id=f"telegram:{external_user_id}",
            source_channel="telegram",
            external_user_id=external_user_id,
            db_session=db_session,
        )
        await self._send_message(chat_id, self._render_result(result.to_dict()), reply_markup=self._keyboard())
        return {
            "ok": True,
            "enabled": self.enabled,
            "handled": True,
            "detail": "message",
            "session_id": result.session_id,
            "lead_id": result.lead_id,
        }

    async def set_webhook(self, url: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "description": "TELEGRAM_BOT_TOKEN is not configured"}
        target = (url or self.webhook_url).strip()
        payload: dict[str, Any] = {"url": target}
        secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
        if secret:
            payload["secret_token"] = secret
        return await self._api("setWebhook", payload)

    async def get_webhook_info(self) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": True, "enabled": False, "description": "TELEGRAM_BOT_TOKEN is not configured"}
        return await self._api("getWebhookInfo", {})

    async def _send_message(self, chat_id: int | str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            LOGGER.warning("Telegram token is not configured; message is not sent")
            return
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:3800],
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await self._api("sendMessage", payload)

    async def _answer_callback(self, callback_id: str | None) -> None:
        if not callback_id or not self.enabled:
            return
        await self._api("answerCallbackQuery", {"callback_query_id": callback_id})

    async def _api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(url, json=payload)
                data = response.json()
                if not response.is_success or not data.get("ok", False):
                    self.last_error = str(data)
                    LOGGER.warning("Telegram API %s failed: %s", method, data)
                return data
        except Exception as exc:
            self.last_error = str(exc)
            LOGGER.warning("Telegram API %s unavailable: %s", method, exc)
            return {"ok": False, "description": str(exc)}

    @staticmethod
    def _keyboard() -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "Продать зерно", "callback_data": "sell_grain"},
                    {"text": "Купить зерно", "callback_data": "buy_grain"},
                ],
                [
                    {"text": "Логистика", "callback_data": "logistics"},
                    {"text": "Хранение", "callback_data": "storage"},
                ],
                [
                    {"text": "ВЭД", "callback_data": "export"},
                    {"text": "Консультация", "callback_data": "consultation"},
                ],
                [{"text": "Связаться с менеджером", "callback_data": "manager"}],
            ]
        }

    @staticmethod
    def _render_result(result: dict[str, Any]) -> str:
        score = result.get("qualification_score", 0)
        next_action = NEXT_ACTION_LABELS.get(result.get("next_action", ""), result.get("next_action", ""))
        missing = ", ".join(result.get("missing_fields") or []) or "нет"
        return f"{result.get('text', '')}\n\nСтатус заявки: {score}%. Следующий шаг: {next_action}. Недостает: {missing}."

    @staticmethod
    def _render_status(status: dict[str, Any]) -> str:
        captured = status.get("captured_fields") or []
        missing = status.get("missing_fields") or []
        score = status.get("qualification_score", 0)
        next_action = NEXT_ACTION_LABELS.get(status.get("next_action", ""), status.get("next_action", ""))
        known_text = "\n".join(f"- {item}" for item in captured) if captured else "Пока нет подтвержденных параметров."
        missing_text = ", ".join(missing) if missing else "нет"
        return f"Карточка заявки\n{known_text}\n\nГотовность: {score}%\nСледующий шаг: {next_action}\nНедостает: {missing_text}"
