"""
Local Model Chat — proxies to llama.cpp servers (Qwen 3 4B, DeepSeek 8B).
Qwen for quick chat, DeepSeek for deep analysis (thinking hidden).
"""

from __future__ import annotations

import os
import re
import time
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger("rmi.local_chat")

router = APIRouter(prefix="/api/v1/chat/local", tags=["local-chat"])

# Local model endpoints (bridge gateway — containers can't reach host 127.0.0.1)
LLAMA_HOST = os.getenv("LLAMA_HOST", "172.22.0.1")
QWEN_URL = f"http://{LLAMA_HOST}:8085/v1/chat/completions"
QWEN_MODEL = "qwen-chat"
DEEPSEEK_URL = f"http://{LLAMA_HOST}:8086/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-analysis"


class LocalChatRequest(BaseModel):
    message: str = Field(..., max_length=2000)
    history: list[dict] = Field(default_factory=list)
    mode: Literal["quick", "deep"] = "quick"
    max_tokens: int = Field(default=1024, ge=64, le=4096)


class LocalChatResponse(BaseModel):
    message: str
    model: str
    provider: str
    mode: str
    latency_ms: int


def _strip_thinking(text: str) -> str:
    """Strip DeepSeek thinking blocks, keep only the final answer."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?answer>", "", text)
    return text.strip()


@router.post("", response_model=LocalChatResponse)
async def local_chat(req: LocalChatRequest):
    """Chat with local models. Quick mode uses Qwen 3 4B, deep mode uses DeepSeek 8B."""
    start = time.time()

    if req.mode == "deep":
        url = DEEPSEEK_URL
        model = DEEPSEEK_MODEL
        timeout = 30.0
    else:
        url = QWEN_URL
        model = QWEN_MODEL
        timeout = 15.0

    # Build messages with history
    messages = [
        {
            "role": "system",
            "content": (
                "You are Rug Munch, an AI crypto intelligence assistant. "
                "You help users understand crypto security, detect scams, analyze tokens, "
                "and answer questions about blockchain, DeFi, and crypto markets. "
                "Be direct, witty, slightly cynical but genuinely helpful. "
                "Never give financial advice. Keep answers concise (2-4 sentences). "
                "If you don't know something, say so honestly."
            ),
        }
    ]
    for msg in req.history[-5:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": req.message})

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                url,
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": req.max_tokens,
                    "temperature": 0.7,
                },
            )
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("local chat unavailable: %s", e)
        raise HTTPException(status_code=503, detail="Local model unavailable")

    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Local model error")

    try:
        raw = r.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Invalid model response")

    choices = raw.get("choices", [])
    if not choices:
        raise HTTPException(status_code=502, detail="Empty model response")

    answer = choices[0].get("message", {}).get("content", "").strip()

    # Strip thinking output from DeepSeek
    if req.mode == "deep":
        answer = _strip_thinking(answer)

    if not answer:
        raise HTTPException(status_code=502, detail="Empty answer")

    latency_ms = int((time.time() - start) * 1000)

    return LocalChatResponse(
        message=answer,
        model=model,
        provider="local",
        mode=req.mode,
        latency_ms=latency_ms,
    )
