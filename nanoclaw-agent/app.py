import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="AgroLead NanoClaw Agent", version="1.0.0")


class AgentChatIn(BaseModel):
    message: str
    context: dict[str, Any] = {}
    transport: str = "http-adapter"
    adapter_endpoint: str = "/api/nanoclaw/agent/chat"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "nanoclaw-agent"}


@app.post("/agent/chat")
async def agent_chat(payload: AgentChatIn) -> dict[str, Any]:
    adapter_url = os.getenv("NANOCLAW_HTTP_ADAPTER_URL", "http://api:8000/api/nanoclaw/agent/chat")
    timeout_seconds = float(os.getenv("NANOCLAW_TIMEOUT_SECONDS", "45"))

    body = {
        "text": payload.message,
        "context": payload.context,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=5.0)) as client:
            response = await client.post(adapter_url, json=body)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"adapter_error: {exc}") from exc

    return {
        "text": (data.get("text") or "").strip(),
        "provider": data.get("provider", "ollama"),
        "model": data.get("model", "unknown"),
        "done": bool(data.get("done", True)),
    }
