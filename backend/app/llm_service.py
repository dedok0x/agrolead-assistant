import base64
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

LOGGER = logging.getLogger("agrolead.llm")


class LLMUnavailableError(RuntimeError):
    pass


def _to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() not in {"0", "false", "no", "off"}


class LLMService:
    def __init__(self) -> None:
        self.provider_mode = os.getenv("LLM_PROVIDER", "auto").strip().lower()
        self.timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45"))

        self.gigachat_client_id = os.getenv("GIGACHAT_CLIENT_ID", "")
        self.gigachat_client_secret = os.getenv("GIGACHAT_CLIENT_SECRET", "")
        self.gigachat_auth_key = os.getenv("GIGACHAT_AUTH_KEY", "")
        self.gigachat_scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        self.gigachat_oauth_url = os.getenv("GIGACHAT_OAUTH_URL", "https://gigachat.devices.sberbank.ru/api/v2/oauth")
        self.gigachat_api_url = os.getenv("GIGACHAT_API_URL", "https://gigachat.devices.sberbank.ru/api/v1")
        self.gigachat_model = os.getenv("GIGACHAT_MODEL", "GigaChat-Max")
        self.gigachat_verify_ssl = _to_bool(os.getenv("GIGACHAT_VERIFY_SSL", "1"), default=True)
        self.gigachat_token_refresh_seconds = int(os.getenv("GIGACHAT_TOKEN_REFRESH_SECONDS", "1500"))

        self.ollama_fallback_enabled = _to_bool(os.getenv("OLLAMA_FALLBACK_ENABLED", "0"), default=False)
        self.ollama_base = os.getenv("OLLAMA_BASE", "http://ollama:11434")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:72b-instruct")
        self.ollama_num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
        self.ollama_num_predict = int(os.getenv("OLLAMA_NUM_PREDICT", "220"))
        self.ollama_temperature = float(os.getenv("OLLAMA_TEMPERATURE", "0.15"))

        self._token = ""
        self._token_expire_ts = 0.0
        self._token_issued_ts = 0.0

        self.usage: dict[str, int] = {"gigachat": 0, "ollama": 0, "fallback": 0}
        self.last_provider = "none"
        self.last_model = "none"
        self.last_reason = "init"
        self.last_error = ""
        self.last_at = ""

        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        timeout = httpx.Timeout(self.timeout_seconds, connect=5.0)
        self._gigachat_client = httpx.AsyncClient(timeout=timeout, limits=limits, verify=self.gigachat_verify_ssl)
        self._ollama_client = httpx.AsyncClient(timeout=timeout, limits=limits)

    async def close(self) -> None:
        await self._gigachat_client.aclose()
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

    def _resolve_basic_auth_key(self) -> str:
        if self.gigachat_auth_key:
            return self.gigachat_auth_key
        if not self.gigachat_client_id or not self.gigachat_client_secret:
            return ""
        raw = f"{self.gigachat_client_id}:{self.gigachat_client_secret}".encode("utf-8")
        return base64.b64encode(raw).decode("utf-8")

    def _gigachat_enabled(self) -> bool:
        if self.provider_mode == "ollama":
            return False
        return bool(self._resolve_basic_auth_key())

    def _ollama_enabled(self) -> bool:
        if self.provider_mode == "gigachat":
            return False
        if self.provider_mode == "ollama":
            return True
        return self.ollama_fallback_enabled

    async def _refresh_gigachat_token(self) -> str:
        now = time.time()
        if (
            self._token
            and (now - self._token_issued_ts) < self.gigachat_token_refresh_seconds
            and now < self._token_expire_ts - 60
        ):
            return self._token

        auth_key = self._resolve_basic_auth_key()
        if not auth_key:
            raise LLMUnavailableError("GigaChat credentials are missing")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid4()),
            "Authorization": f"Basic {auth_key}",
        }
        response = await self._gigachat_client.post(self.gigachat_oauth_url, headers=headers, data={"scope": self.gigachat_scope})
        response.raise_for_status()

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise LLMUnavailableError("GigaChat token is empty")

        expires_at = payload.get("expires_at")
        if isinstance(expires_at, (int, float)):
            self._token_expire_ts = float(expires_at) / 1000.0 if expires_at > 10_000_000_000 else float(expires_at)
        else:
            self._token_expire_ts = now + 25 * 60

        self._token = token
        self._token_issued_ts = now
        return token

    async def _chat_gigachat(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        token = await self._refresh_gigachat_token()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload: dict[str, Any] = {
            "model": self.gigachat_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": self.ollama_num_predict,
        }
        response = await self._gigachat_client.post(f"{self.gigachat_api_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailableError("GigaChat returned empty choices")

        content = ((choices[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise LLMUnavailableError("GigaChat returned empty content")
        return content, self.gigachat_model

    async def _chat_ollama(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        full_prompt = f"{system_prompt}\n\n{user_prompt}\nАссистент:"
        response = await self._ollama_client.post(
            f"{self.ollama_base}/api/generate",
            json={
                "model": self.ollama_model,
                "prompt": full_prompt,
                "stream": False,
                "keep_alive": "30m",
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
            raise LLMUnavailableError("Ollama returned empty content")
        return content, self.ollama_model

    async def complete(self, system_prompt: str, user_prompt: str, reason: str = "chat") -> tuple[str, str, str]:
        attempts: list[str] = []

        if self._gigachat_enabled():
            try:
                text, model = await self._chat_gigachat(system_prompt=system_prompt, user_prompt=user_prompt)
                self._mark("gigachat", model, reason)
                LOGGER.info("LLM response provider=gigachat model=%s reason=%s", model, reason)
                return text, "gigachat", model
            except Exception as exc:
                attempts.append(f"gigachat={exc}")
                LOGGER.warning("GigaChat failed: %s", exc)
                if self.provider_mode == "gigachat":
                    self._mark("fallback", "none", reason, error=str(exc))
                    raise LLMUnavailableError(f"GigaChat error: {exc}") from exc
        elif self.provider_mode == "gigachat":
            message = "GigaChat mode enabled but credentials are missing"
            self._mark("fallback", "none", reason, error=message)
            raise LLMUnavailableError(message)

        if self._ollama_enabled():
            try:
                text, model = await self._chat_ollama(system_prompt=system_prompt, user_prompt=user_prompt)
                self._mark("ollama", model, reason)
                LOGGER.info("LLM response provider=ollama model=%s reason=%s", model, reason)
                return text, "ollama", model
            except Exception as exc:
                attempts.append(f"ollama={exc}")
                self._mark("fallback", "none", reason, error=str(exc))
                LOGGER.warning("Ollama failed: %s", exc)
                raise LLMUnavailableError(f"Ollama error: {exc}") from exc

        details = " | ".join(attempts) if attempts else "no available providers"
        self._mark("fallback", "none", reason, error=details)
        raise LLMUnavailableError(f"LLM unavailable: {details}")

    def status(self) -> dict[str, Any]:
        preferred_provider = "gigachat" if self._gigachat_enabled() else ("ollama" if self._ollama_enabled() else "none")
        fallback_provider = "ollama" if preferred_provider == "gigachat" and self._ollama_enabled() else None
        return {
            "mode": self.provider_mode,
            "preferred_provider": preferred_provider,
            "fallback_provider": fallback_provider,
            "gigachat_ready": self._gigachat_enabled(),
            "ollama_enabled": self._ollama_enabled(),
            "models": {
                "gigachat": self.gigachat_model,
                "ollama": self.ollama_model,
            },
            "last_provider": self.last_provider,
            "last_model": self.last_model,
            "last_reason": self.last_reason,
            "last_error": self.last_error,
            "last_at": self.last_at,
            "usage": self.usage,
        }
