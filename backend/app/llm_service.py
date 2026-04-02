import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx


class LLMService:
    def __init__(self) -> None:
        self.provider_mode = os.getenv("LLM_PROVIDER", "auto").lower()
        self.timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45"))

        self.gigachat_auth_key = os.getenv("GIGACHAT_AUTH_KEY", "")
        self.gigachat_scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        self.gigachat_oauth_url = os.getenv("GIGACHAT_OAUTH_URL", "https://gigachat.devices.sberbank.ru/api/v2/oauth")
        self.gigachat_api_url = os.getenv("GIGACHAT_API_URL", "https://gigachat.devices.sberbank.ru/api/v1")
        self.gigachat_model = os.getenv("GIGACHAT_MODEL", "GigaChat-Max")
        self.gigachat_verify_ssl = os.getenv("GIGACHAT_VERIFY_SSL", "1") not in {"0", "false", "False"}
        self.gigachat_token_refresh_seconds = int(os.getenv("GIGACHAT_TOKEN_REFRESH_SECONDS", "1500"))

        self.ollama_base = os.getenv("OLLAMA_BASE", "http://ollama:11434")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:72b-instruct")
        self.ollama_num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
        self.ollama_num_predict = int(os.getenv("OLLAMA_NUM_PREDICT", "220"))
        self.ollama_temperature = float(os.getenv("OLLAMA_TEMPERATURE", "0.15"))

        self._token = ""
        self._token_expire_ts = 0.0
        self._token_issued_ts = 0.0

        self.usage: dict[str, int] = {"gigachat": 0, "ollama": 0, "fallback": 0}
        self.last_provider = "unknown"
        self.last_model = "unknown"
        self.last_reason = "init"
        self.last_at = ""

        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        timeout = httpx.Timeout(self.timeout_seconds, connect=5.0)
        self._gigachat_client = httpx.AsyncClient(timeout=timeout, limits=limits, verify=self.gigachat_verify_ssl)
        self._ollama_client = httpx.AsyncClient(timeout=timeout, limits=limits)

    async def close(self) -> None:
        await self._gigachat_client.aclose()
        await self._ollama_client.aclose()

    def _mark(self, provider: str, model: str, reason: str) -> None:
        if provider not in self.usage:
            self.usage[provider] = 0
        self.usage[provider] += 1
        self.last_provider = provider
        self.last_model = model
        self.last_reason = reason
        self.last_at = datetime.now(timezone.utc).isoformat()

    def _gigachat_enabled(self) -> bool:
        if self.provider_mode == "ollama":
            return False
        if self.provider_mode == "gigachat":
            return True
        return bool(self.gigachat_auth_key)

    async def _refresh_gigachat_token(self) -> str:
        now = time.time()
        if (
            self._token
            and (now - self._token_issued_ts) < self.gigachat_token_refresh_seconds
            and now < self._token_expire_ts - 60
        ):
            return self._token

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid4()),
            "Authorization": f"Basic {self.gigachat_auth_key}",
        }
        resp = await self._gigachat_client.post(self.gigachat_oauth_url, headers=headers, data={"scope": self.gigachat_scope})
        resp.raise_for_status()
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("GigaChat token is empty")

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
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        payload: dict[str, Any] = {
            "model": self.gigachat_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": self.ollama_num_predict,
        }
        resp = await self._gigachat_client.post(f"{self.gigachat_api_url}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Empty choices from GigaChat")
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
        if not text:
            raise RuntimeError("Empty content from GigaChat")
        return text, self.gigachat_model

    async def _chat_ollama(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        full_prompt = f"{system_prompt}\n\n{user_prompt}\nАссистент:"
        resp = await self._ollama_client.post(
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
        resp.raise_for_status()
        text = (resp.json().get("response") or "").strip()
        if not text:
            raise RuntimeError("Empty response from Ollama")
        return text, self.ollama_model

    async def complete(self, system_prompt: str, user_prompt: str, reason: str = "chat") -> tuple[str, str, str]:
        if self._gigachat_enabled():
            try:
                text, model = await self._chat_gigachat(system_prompt=system_prompt, user_prompt=user_prompt)
                self._mark("gigachat", model, reason)
                return text, "gigachat", model
            except Exception:
                if self.provider_mode == "gigachat":
                    raise

        try:
            text, model = await self._chat_ollama(system_prompt=system_prompt, user_prompt=user_prompt)
            self._mark("ollama", model, reason)
            return text, "ollama", model
        except Exception:
            self._mark("fallback", "none", reason)
            raise

    def status(self) -> dict[str, Any]:
        preferred = "gigachat" if self._gigachat_enabled() else "ollama"
        return {
            "mode": self.provider_mode,
            "preferred": preferred,
            "fallback": "ollama" if preferred == "gigachat" else None,
            "models": {
                "gigachat": self.gigachat_model,
                "ollama": self.ollama_model,
            },
            "last_provider": self.last_provider,
            "last_model": self.last_model,
            "last_reason": self.last_reason,
            "last_at": self.last_at,
            "usage": self.usage,
        }

