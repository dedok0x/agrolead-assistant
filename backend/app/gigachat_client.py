import os
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx


class GigaChatClientError(RuntimeError):
    pass


class GigaChatAuthError(GigaChatClientError):
    pass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


class GigaChatClient:
    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.auth_key = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
        self.scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
        self.auth_url = os.getenv(
            "GIGACHAT_AUTH_URL",
            "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        ).strip()
        self.api_base_url = os.getenv(
            "GIGACHAT_API_BASE_URL",
            "https://gigachat.devices.sberbank.ru/api/v1",
        ).strip().rstrip("/")
        self.model = os.getenv("GIGACHAT_MODEL", "GigaChat-2").strip()
        self.verify_ssl = _env_bool("GIGACHAT_VERIFY_SSL", True)
        self.ca_file = os.getenv("GIGACHAT_CA_FILE", "").strip()
        self.insecure_ssl_fallback = _env_bool("GIGACHAT_INSECURE_SSL_FALLBACK", False)
        self.token_refresh_margin_seconds = max(
            30,
            min(int(os.getenv("GIGACHAT_TOKEN_REFRESH_MARGIN_SECONDS", "120")), 600),
        )

        timeout_seconds = max(1.0, min(float(timeout_seconds), 5.0))
        timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0))
        verify = self.ca_file or self.verify_ssl
        self._client = httpx.AsyncClient(timeout=timeout, verify=verify)

        self._access_token = ""
        self._expires_at = datetime.now(timezone.utc)

    def _is_ssl_error(self, exc: Exception) -> bool:
        return "certificate verify failed" in str(exc).lower()

    async def _post(
        self,
        url: str,
        headers: dict[str, str],
        *,
        data: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            return await self._client.post(url, headers=headers, data=data, json=json)
        except Exception as exc:
            if not self.insecure_ssl_fallback or not self._is_ssl_error(exc):
                raise

            timeout = self._client.timeout
            async with httpx.AsyncClient(timeout=timeout, verify=False) as insecure_client:
                return await insecure_client.post(url, headers=headers, data=data, json=json)

    @property
    def configured(self) -> bool:
        return bool(self.auth_key)

    async def close(self) -> None:
        await self._client.aclose()

    def _token_valid(self) -> bool:
        if not self._access_token:
            return False
        return datetime.now(timezone.utc) + timedelta(seconds=self.token_refresh_margin_seconds) < self._expires_at

    def _resolve_expiry(self, payload: dict[str, Any]) -> datetime:
        now = datetime.now(timezone.utc)
        hard_cap = now + timedelta(minutes=30)
        resolved: datetime | None = None

        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            resolved = now + timedelta(seconds=float(expires_in))

        expires_at = payload.get("expires_at")
        if resolved is None:
            if isinstance(expires_at, (int, float)) and expires_at > 0:
                if expires_at > 1_000_000_000_000:
                    resolved = datetime.fromtimestamp(expires_at / 1000, tz=timezone.utc)
                else:
                    resolved = datetime.fromtimestamp(expires_at, tz=timezone.utc)
            elif isinstance(expires_at, str) and expires_at:
                try:
                    fixed = expires_at.replace("Z", "+00:00")
                    resolved = datetime.fromisoformat(fixed).astimezone(timezone.utc)
                except ValueError:
                    resolved = None

        if resolved is None:
            resolved = hard_cap

        # У токена GigaChat фактический TTL ~30 минут, не держим дольше.
        if resolved > hard_cap:
            resolved = hard_cap

        min_valid_until = now + timedelta(seconds=30)
        if resolved < min_valid_until:
            resolved = min_valid_until

        return resolved

    async def _refresh_token(self) -> str:
        if not self.auth_key:
            raise GigaChatAuthError("GIGACHAT_AUTH_KEY is empty")

        headers = {
            "Authorization": f"Basic {self.auth_key}",
            "RqUID": str(uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        response = await self._post(self.auth_url, headers=headers, data={"scope": self.scope})
        if response.status_code >= 400:
            raise GigaChatAuthError(f"OAuth failed with status={response.status_code}: {response.text}")

        payload = response.json()
        token = (payload.get("access_token") or "").strip()
        if not token:
            raise GigaChatAuthError("OAuth response does not contain access_token")

        self._access_token = token
        self._expires_at = self._resolve_expiry(payload)
        return token

    async def _get_access_token(self, force_refresh: bool = False) -> str:
        if not force_refresh and self._token_valid():
            return self._access_token
        return await self._refresh_token()

    async def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 220,
    ) -> tuple[str, str]:
        if not self.configured:
            raise GigaChatAuthError("GigaChat is not configured")

        endpoint = f"{self.api_base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error = ""
        for attempt in range(2):
            token = await self._get_access_token(force_refresh=attempt > 0)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }

            response = await self._post(endpoint, headers=headers, json=payload)
            if response.status_code == 401 and attempt == 0:
                self._access_token = ""
                self._expires_at = datetime.now(timezone.utc)
                continue

            if response.status_code >= 400:
                last_error = f"status={response.status_code} body={response.text}"
                break

            data = response.json()
            content = ""
            choices = data.get("choices") or []
            if choices:
                first = choices[0] or {}
                message = first.get("message") or {}
                content = (message.get("content") or "").strip()

            if not content:
                content = (data.get("text") or data.get("answer") or "").strip()

            if not content:
                raise GigaChatClientError("GigaChat returned empty content")

            model = (data.get("model") or self.model or "gigachat").strip()
            return content, model

        if not last_error:
            last_error = "unauthorized"
        raise GigaChatClientError(f"GigaChat completion failed: {last_error}")
