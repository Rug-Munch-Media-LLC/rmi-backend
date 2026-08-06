#!/usr/bin/env python3
"""
Stub AI Router - Intelligent Model-First Provider Swapping
===========================================================
Routes requests to optimal AI provider based on quota, latency, and cost.
For now, a minimal stub that delegates to OpenRouter.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["AI Router"])

# Decode base64 LLM key if present, otherwise use plain LLM_API_KEY
# (safety net: ensures key is decoded even when imported without main.py)
if os.getenv("LLM_API_KEY_B64"):
    os.environ["LLM_API_KEY"] = base64.b64decode(os.getenv("LLM_API_KEY_B64")).decode()

# Model tiers (for reference, full config in ai_router.py)
MODEL_TIERS = {
    "T0": {"name": "Ultra", "models": ["gpt-4o", "claude-3.5-sonnet"], "max_cost_per_1k": 0.05},
    "T1": {"name": "Premium", "models": ["gpt-4-turbo", "claude-3-opus"], "max_cost_per_1k": 0.02},
    "T2": {
        "name": "Standard",
        "models": ["gpt-3.5-turbo", "claude-3-haiku"],
        "max_cost_per_1k": 0.005,
    },
    "T3": {"name": "Fast", "models": ["llama-3-8b", "mistral-tiny"], "max_cost_per_1k": 0.001},
    "T4": {"name": "Free", "models": ["tiny-llama", "phi-2"], "max_cost_per_1k": 0.0},
}

# Providers (for reference)
PROVIDERS = {
    "deepseek": {
        "url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions"),
        "key_env": "LLM_API_KEY",
        "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
        "rpm": 100,
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "rpm": 100,
    },
}


@router.post("/ai/completions")
async def ai_completions(request: dict[str, Any]) -> dict[str, str]:
    """AI completion via optimal provider routing."""
    return {"error": "AI Router not fully configured", "provider": "openrouter"}


@router.post("/ai/chat")
async def ai_chat(request: dict[str, Any]) -> dict[str, str]:
    """AI chat endpoint with provider fallback."""
    return {"error": "AI Router not fully configured", "provider": "openrouter"}


@router.get("/ai/providers")
async def list_providers() -> dict[str, list]:
    """List available AI providers."""
    return {"providers": list(PROVIDERS.keys())}


@router.get("/ai/models")
async def list_models() -> dict:
    """List available models by tier."""
    return {"tiers": MODEL_TIERS}


# ── Direct chat completion (used by app.routers.chat) ──


async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    tier: str = "T2",
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Call the LLM API and return a chat completion.

    Uses DeepSeek (preferred) or OpenRouter as fallback.
    The response includes 'choices' with message content.
    """

    import httpx

    primary = PROVIDERS["deepseek"]
    api_key = os.getenv(primary["key_env"], "")

    if api_key:
        url = primary["url"]
        provider_key = api_key
        provider_model = model or primary["model"]
    else:
        # Fallback to OpenRouter
        fallback = PROVIDERS["openrouter"]
        url = fallback["url"]
        provider_key = os.getenv(fallback["key_env"], "")
        provider_model = model or os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")

    if not provider_key:
        from app.core.errors import ExternalAPIError

        raise ExternalAPIError(message="AI provider key not configured. Set LLM_API_KEY or OPENROUTER_API_KEY.")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            json={
                "model": provider_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            headers={
                "Authorization": f"Bearer {provider_key}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            from app.core.errors import ExternalAPIError

            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            raise ExternalAPIError(message=f"AI provider returned {resp.status_code}: {detail}")

        data = resp.json()
        choices = data.get("choices", [])
        content = ""
        if choices and "message" in choices[0]:
            content = choices[0]["message"].get("content", "")
        return {
            "id": data.get("id", "chat-unknown"),
            "content": content,
            "choices": choices,
            "model": data.get("model", provider_model),
            "usage": data.get("usage", {}),
            "_provider": "deepseek"
            if api_key == getattr(os, "environ", {}).get(primary["key_env"], "")
            else "openrouter",
            "_latency_ms": int(resp.elapsed.total_seconds() * 1000),
        }
