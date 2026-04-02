import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

LOGGER = logging.getLogger("agrolead.llm")


class LLMUnavailableError(RuntimeError):
    pass


class LLMService:
    def __init__(self) -> None:
        self.provider_mode = "ollama"
        self.timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45"))

        self.ollama_base = os.getenv("OLLAMA_BASE", "http://ollama:11434").strip().rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "tinyllama").strip()
        self.ollama_num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "512"))
        self.ollama_num_predict = int(os.getenv("OLLAMA_NUM_PREDICT", "96"))
        self.ollama_temperature = float(os.getenv("OLLAMA_TEMPERATURE", "0.15"))

        self.usage: dict[str, int] = {"ollama": 0, "fallback": 0}
        self.last_provider = "none"
        self.last_model = "none"
        self.last_reason = "init"
        self.last_error = ""
        self.last_at = ""

        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        timeout = httpx.Timeout(self.timeout_seconds, connect=5.0)
        self._ollama_client = httpx.AsyncClient(timeout=timeout, limits=limits)

    async def close(self) -> None:
        await self._ollama_client.aclose()

    def _mark(self, provider: str, model: str, reason: str, error: str = "") -> None:
        if provider not in self.usage:
            self.usage[provider] = 0
        self.usage[provider] += 1
        self.last_provider = provider
        self.last_model = model
        self.last_reason = reason
        self.last_error = error
        self.last_at = datetime.now(timezone.utc).isoformat()

    async def _chat_ollama_generate(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        full_prompt = f"{system_prompt}\n\n{user_prompt}\nАссистент:"
        response = await self._ollama_client.post(
            f"{self.ollama_base}/api/generate",
            json={
                "model": self.ollama_model,
                "prompt": full_prompt,
                "stream": False,
                "keep_alive": "10m",
                "options": {
                    "temperature": self.ollama_temperature,
                    "num_predict": self.ollama_num_predict,
                    "num_ctx": self.ollama_num_ctx,
                    "repeat_penalty": 1.1,
                },
            },
        )
        response.raise_for_status()
        content = (response.json().get("response") or "").strip()
        if not content:
            raise LLMUnavailableError("Ollama returned empty content from /api/generate")
        return content, self.ollama_model

    async def _chat_ollama_chat(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        response = await self._ollama_client.post(
            f"{self.ollama_base}/api/chat",
            json={
                "model": self.ollama_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "keep_alive": "10m",
                "options": {
                    "temperature": self.ollama_temperature,
                    "num_predict": self.ollama_num_predict,
                    "num_ctx": self.ollama_num_ctx,
                    "repeat_penalty": 1.1,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = ((payload.get("message") or {}).get("content") or "").strip()
        if not content:
            raise LLMUnavailableError("Ollama returned empty content from /api/chat")
        return content, self.ollama_model

    async def _chat_ollama_openai(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        response = await self._ollama_client.post(
            f"{self.ollama_base}/v1/chat/completions",
            json={
                "model": self.ollama_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self.ollama_temperature,
                "max_tokens": self.ollama_num_predict,
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""
        if not content:
            raise LLMUnavailableError("Ollama returned empty content from /v1/chat/completions")
        return content, self.ollama_model

    async def _chat_ollama(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        errors: list[str] = []
        for strategy in (self._chat_ollama_generate, self._chat_ollama_chat, self._chat_ollama_openai):
            try:
                return await strategy(system_prompt=system_prompt, user_prompt=user_prompt)
            except Exception as exc:
                errors.append(f"{strategy.__name__}: {exc}")
        raise LLMUnavailableError("Ollama unavailable: " + " | ".join(errors))

    async def complete(self, system_prompt: str, user_prompt: str, reason: str = "chat") -> tuple[str, str, str]:
        try:
            text, model = await self._chat_ollama(system_prompt=system_prompt, user_prompt=user_prompt)
            self._mark("ollama", model, reason)
            LOGGER.info("LLM response provider=ollama model=%s reason=%s", model, reason)
            return text, "ollama", model
        except Exception as exc:
            self._mark("fallback", "none", reason, error=str(exc))
            LOGGER.warning("Ollama failed: %s", exc)
            raise LLMUnavailableError(f"Ollama error: {exc}") from exc

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.provider_mode,
            "preferred_provider": "ollama",
            "fallback_provider": None,
            "ollama_enabled": True,
            "models": {
                "ollama": self.ollama_model,
            },
            "last_provider": self.last_provider,
            "last_model": self.last_model,
            "last_reason": self.last_reason,
            "last_error": self.last_error,
            "last_at": self.last_at,
            "usage": self.usage,
        }
