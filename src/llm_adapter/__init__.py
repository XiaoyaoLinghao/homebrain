"""
HomeBrain v2.0 — LLM Adapter Module

Natural language → device control bridge using LLM function calling.
Integrates with Home Assistant via HABridgeClient (P1-T1).

Version: 0.1.0
"""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .adapter import LLMAdapter, DeepSeekClient
from .ha_context import HAContextBuilder

__version__ = "0.1.0"
__all__ = ["LLMAdapter", "HAContextBuilder", "router"]

router = APIRouter(prefix="/api/llm", tags=["llm"])


# ── request / response models ────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., description="用户自然语言指令", min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    reply: str
    tool_calls: Optional[list] = None
    call_results: Optional[list] = None


class HealthResponse(BaseModel):
    status: str
    version: str


# ── dependency ───────────────────────────────────────────────────────

_adapter: Optional[LLMAdapter] = None


def get_adapter() -> LLMAdapter:
    """Lazy-init the LLMAdapter singleton using environment config."""
    global _adapter
    if _adapter is None:
        from ..ha_bridge import HABridgeClient

        ha_client = HABridgeClient()
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

        if api_key:
            llm_client = DeepSeekClient(api_key=api_key, model=model)
        else:
            llm_client = None  # adapter will try DeepSeekClient defaults

        _adapter = LLMAdapter(ha_client, llm_client=llm_client)
    return _adapter


# ── endpoints ────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok", "version": __version__}


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """POST /api/llm/chat — 自然语言控制家居

    Request:
        {"message": "打开客厅灯"}

    Response:
        {
            "reply": "已打开客厅灯",
            "tool_calls": [{"name": "ha_light_turn_on", "arguments": {"entity_id": "light.living_room"}}],
            "call_results": [{"success": true, ...}]
        }
    """
    try:
        adapter = get_adapter()
        result = await adapter.process(request.message)
        return ChatResponse(
            reply=result["reply"],
            tool_calls=result.get("tool_calls"),
            call_results=result.get("call_results"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
