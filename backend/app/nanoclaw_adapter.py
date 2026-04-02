import os
from typing import Any

import httpx


class NanoClawAdapter:
    def __init__(self) -> None:
        self.base_url = os.getenv("NANOCLAW_BASE_URL", "http://nanoclaw:8788")
        self.timeout_seconds = float(os.getenv("NANOCLAW_TIMEOUT_SECONDS", "45"))
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=5.0))

    async def close(self) -> None:
        await self.client.aclose()

    async def chat(self, message: str, context: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "message": message,
            "context": context,
            "transport": "http-adapter",
            "adapter_endpoint": "/api/nanoclaw/agent/chat",
        }
        resp = await self.client.post(f"{self.base_url}/agent/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return {
            "text": data.get("text", "").strip(),
            "provider": data.get("provider", "nanoclaw"),
            "model": data.get("model", "nanoclaw-runtime"),
            "raw": data,
        }

