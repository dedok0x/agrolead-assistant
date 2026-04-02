import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from .gigachat_client import GigaChatClient, GigaChatClientError

LOGGER = logging.getLogger("agrolead.llm")


class LLMUnavailableError(RuntimeError):
    pass


def _contains_cyrillic(text: str) -> bool:
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


class LLMService:
    def __init__(self) -> None:
        preferred = os.getenv("LLM_PROVIDER", "gigachat").strip().lower()
        self.preferred_provider = preferred if preferred in {"gigachat", "template"} else "gigachat"

        self.timeout_seconds = max(1.0, min(float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "5")), 5.0))
        self.max_retries = max(0, min(int(os.getenv("LLM_MAX_RETRIES", "1")), 2))
        self.enable_fallback = _env_bool("LLM_TEMPLATE_FALLBACK_ENABLED", True)
        self.enable_rewrite = _env_bool("LLM_REWRITE_ENABLED", True)

        self.gigachat_client = GigaChatClient(timeout_seconds=self.timeout_seconds)
        self.gigachat_model = self.gigachat_client.model
        self._inference_lock = asyncio.Lock()

        self.usage: dict[str, int] = {"gigachat": 0, "template": 0, "errors": 0}
        self.last_provider = "none"
        self.last_model = "none"
        self.last_reason = "init"
        self.last_error = ""
        self.last_at = ""

    async def close(self) -> None:
        await self.gigachat_client.close()

    def _mark(self, provider: str, model: str, reason: str, error: str = "") -> None:
        if provider not in self.usage:
            self.usage[provider] = 0
        self.usage[provider] += 1
        self.last_provider = provider
        self.last_model = model
        self.last_reason = reason
        self.last_error = error
        self.last_at = datetime.now(timezone.utc).isoformat()

    def _enforce_russian(self, text: str) -> str:
        normalized = (text or "").strip()
        if not normalized:
            return "Принял запрос. Подготовлю ответ по зерновой заявке и следующему шагу."
        if _contains_cyrillic(normalized):
            return normalized
        return "Отвечаю на русском: принял запрос по зерновой сделке, уточню детали и передам менеджеру."

    def _template_answer(self, user_prompt: str, reason: str) -> str:
        prompt = (user_prompt or "").strip().lower()
        if "цена" in prompt or "прайс" in prompt or "стоим" in prompt:
            return "По цене ориентируемся от объема и базиса. Дайте культуру, класс, тоннаж и регион доставки."
        if "контакт" in prompt or "тел" in prompt or "email" in prompt:
            return "Оставьте телефон или email, менеджер закрепит условия и свяжется с вами."
        if "кто" in prompt or "компан" in prompt:
            return "Работаем по зерновым сделкам: подбор позиции, логистика и фиксация заявки менеджером."
        if reason == "rewrite":
            return self._enforce_russian(user_prompt)
        return "Понял. Чтобы быстро собрать заявку, укажите товар, класс, объем, регион и срок отгрузки."

    async def _complete_gigachat(self, system_prompt: str, user_prompt: str, reason: str) -> tuple[str, str, str]:
        final_system = (
            "Отвечай только на русском языке, коротко и по делу. "
            "Не выдумывай факты, цены и остатки. "
            f"{system_prompt}"
        )

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                text, model = await self.gigachat_client.chat_completion(
                    system_prompt=final_system,
                    user_prompt=user_prompt,
                    temperature=0.1,
                    max_tokens=220,
                )
                safe_text = self._enforce_russian(text)
                self._mark("gigachat", model, reason)
                return safe_text, "gigachat", model
            except Exception as exc:
                last_exc = exc
                LOGGER.warning("GigaChat attempt %s failed: %s", attempt + 1, exc)

        self._mark("errors", "none", reason, error=str(last_exc) if last_exc else "unknown")
        raise LLMUnavailableError(str(last_exc) if last_exc else "GigaChat unavailable")

    async def complete(self, system_prompt: str, user_prompt: str, reason: str = "chat") -> tuple[str, str, str]:
        provider = self.preferred_provider
        if provider == "gigachat" and self.gigachat_client.configured:
            try:
                async with self._inference_lock:
                    return await self._complete_gigachat(system_prompt=system_prompt, user_prompt=user_prompt, reason=reason)
            except (LLMUnavailableError, GigaChatClientError) as exc:
                LOGGER.warning("Provider fallback to template: %s", exc)
                if not self.enable_fallback:
                    raise LLMUnavailableError(str(exc)) from exc
        elif provider == "gigachat" and not self.gigachat_client.configured and not self.enable_fallback:
            raise LLMUnavailableError("GigaChat is not configured")

        text = self._template_answer(user_prompt=user_prompt, reason=reason)
        text = self._enforce_russian(text)
        self._mark("template", "deterministic-template", reason)
        return text, "template", "deterministic-template"

    async def rewrite_response(self, source_text: str, reason: str = "rewrite") -> tuple[str, str, str]:
        if not self.enable_rewrite:
            text = self._enforce_russian(source_text)
            self._mark("template", "deterministic-template", reason)
            return text, "template", "deterministic-template"

        rewrite_prompt = (
            "Перепиши ответ продавца естественно, без канцелярита, максимум 2 предложения. "
            "Сохрани факты и следующий шаг.\n"
            f"Черновик: {source_text}"
        )
        return await self.complete(
            system_prompt="Ты B2B ассистент по зерновым сделкам.",
            user_prompt=rewrite_prompt,
            reason=reason,
        )

    def status(self) -> dict[str, Any]:
        return {
            "mode": "provider-router",
            "preferred_provider": self.preferred_provider,
            "fallback_provider": "template",
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "gigachat_enabled": self.gigachat_client.configured,
            "models": {
                "gigachat": self.gigachat_model,
                "template": "deterministic-template",
            },
            "last_provider": self.last_provider,
            "last_model": self.last_model,
            "last_reason": self.last_reason,
            "last_error": self.last_error,
            "last_at": self.last_at,
            "usage": self.usage,
        }
