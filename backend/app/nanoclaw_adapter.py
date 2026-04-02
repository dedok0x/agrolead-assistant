import os
from typing import Any

import httpx


class NanoClawAdapterError(RuntimeError):
    pass


class NanoClawAdapter:
    def __init__(self) -> None:
        self.base_url = os.getenv("NANOCLAW_BASE_URL", "http://nanoclaw-agent:8788").rstrip("/")
        self.chat_path = os.getenv("NANOCLAW_AGENT_CHAT_PATH", "/agent/chat")
        self.timeout_seconds = float(os.getenv("NANOCLAW_TIMEOUT_SECONDS", "45"))
        timeout = httpx.Timeout(self.timeout_seconds, connect=5.0)
        self.client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self.client.aclose()

    async def chat(self, message: str, context: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "message": message,
            "context": context,
            "transport": "http-adapter",
            "adapter_endpoint": "/api/nanoclaw/agent/chat",
        }
        url = f"{self.base_url}{self.chat_path}"
        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise NanoClawAdapterError(f"NanoClaw transport error: {exc}") from exc

        data = response.json()
        text = (data.get("text") or "").strip()
        if not text:
            raise NanoClawAdapterError("NanoClaw returned empty text")

        return {
            "text": text,
            "provider": data.get("provider", "nanoclaw"),
            "model": data.get("model", "nanoclaw-runtime"),
            "raw": data,
        }
