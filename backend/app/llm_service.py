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


class LLMService:
    def __init__(self) -> None:
        self.preferred_provider = os.getenv("LLM_PROVIDER", "gigachat").strip().lower() or "gigachat"
        self.timeout_seconds = max(1.0, min(float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "5")), 5.0))
        self.max_retries = max(0, min(int(os.getenv("LLM_MAX_RETRIES", "1")), 2))
        self.enable_rewrite = os.getenv("LLM_REWRITE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
        self.max_parallel_inference = max(1, min(int(os.getenv("LLM_MAX_PARALLEL_INFERENCE", "4")), 16))

        self.gigachat_client = GigaChatClient(timeout_seconds=self.timeout_seconds)
        self.gigachat_model = self.gigachat_client.model
        self._inference_semaphore = asyncio.Semaphore(self.max_parallel_inference)

        self.usage: dict[str, int] = {"gigachat": 0, "errors": 0}
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
            return "Принял. Уточню детали по сделке и дам следующий шаг."
        if _contains_cyrillic(normalized):
            return normalized
        return "Отвечаю на русском: получил запрос, готов продолжать диалог по сделке."

    async def _complete_gigachat(
        self,
        system_prompt: str,
        user_prompt: str,
        reason: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str, str]:
        final_system = (
            "Отвечай только на русском языке. "
            "Пиши живо, предметно и без шаблонного канцелярита. "
            "Не повторяй дословно предыдущие ответы и не выдумывай факты. "
            f"{system_prompt}"
        )

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                text, model = await self.gigachat_client.chat_completion(
                    system_prompt=final_system,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                safe_text = self._enforce_russian(text)
                self._mark("gigachat", model, reason)
                return safe_text, "gigachat", model
            except Exception as exc:
                last_exc = exc
                LOGGER.warning("GigaChat attempt %s failed: %s", attempt + 1, exc)

        self._mark("errors", "none", reason, error=str(last_exc) if last_exc else "unknown")
        raise LLMUnavailableError(str(last_exc) if last_exc else "GigaChat unavailable")

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        reason: str = "chat",
        temperature: float = 0.55,
        max_tokens: int = 320,
    ) -> tuple[str, str, str]:
        if self.preferred_provider != "gigachat":
            self._mark("errors", "none", reason, error=f"Unsupported provider: {self.preferred_provider}")
            raise LLMUnavailableError(f"Unsupported provider: {self.preferred_provider}")

        if not self.gigachat_client.configured:
            self._mark("errors", "none", reason, error="GigaChat is not configured")
            raise LLMUnavailableError("GigaChat is not configured")

        try:
            async with self._inference_semaphore:
                return await self._complete_gigachat(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    reason=reason,
                    temperature=max(0.05, min(temperature, 1.2)),
                    max_tokens=max(64, min(max_tokens, 700)),
                )
        except (LLMUnavailableError, GigaChatClientError) as exc:
            raise LLMUnavailableError(str(exc)) from exc

    async def rewrite_response(self, source_text: str, reason: str = "rewrite") -> tuple[str, str, str]:
        if not self.enable_rewrite:
            text = self._enforce_russian(source_text)
            self._mark("gigachat", self.gigachat_model, reason)
            return text, "gigachat", self.gigachat_model

        rewrite_prompt = (
            "Перепиши ответ продавца более живо и естественно, максимум 2 предложения. "
            "Сохрани факты, не добавляй новые условия.\n"
            f"Черновик: {source_text}"
        )
        return await self.complete(
            system_prompt="Ты B2B ассистент по зерновым сделкам.",
            user_prompt=rewrite_prompt,
            reason=reason,
            temperature=0.42,
            max_tokens=220,
        )

    def status(self) -> dict[str, Any]:
        return {
            "mode": "provider-router",
            "preferred_provider": self.preferred_provider,
            "fallback_provider": None,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_parallel_inference": self.max_parallel_inference,
            "gigachat_enabled": self.gigachat_client.configured,
            "models": {
                "gigachat": self.gigachat_model,
            },
            "last_provider": self.last_provider,
            "last_model": self.last_model,
            "last_reason": self.last_reason,
            "last_error": self.last_error,
            "last_at": self.last_at,
            "usage": self.usage,
        }
