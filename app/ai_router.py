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
    tier: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Call the LLM API and return a chat completion.

    Uses OpenRouter (preferred) or local Ollama as fallback.
    When model is specified, uses that model. Otherwise uses the configured default.
    The 'tier' parameter is accepted for API compatibility but model routing is handled
    by app.core.model_router when called separately.
    """

    import httpx

    # Try OpenRouter first (primary gateway for all models)
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    deepseek_key = os.getenv("LLM_API_KEY", "")

    if openrouter_key:
        url = "https://openrouter.ai/api/v1/chat/completions"
        provider_key = openrouter_key
        provider_model = model or os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
        provider_name = "openrouter"
    elif deepseek_key:
        url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
        provider_key = deepseek_key
        provider_model = model or os.getenv("LLM_MODEL", "deepseek-v4-flash")
        provider_name = "deepseek"
    else:
        # Try local Ollama as last resort
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": model or "qwen3:4b",
                        "prompt": messages[-1]["content"] if messages else "",
                        "stream": False,
                    },
                )
                if r.status_code == 200:
                    resp_data = r.json()
                    return {
                        "id": "ollama-local",
                        "content": resp_data.get("response", ""),
                        "choices": [{"message": {"content": resp_data.get("response", "")}}],
                        "model": model or "qwen3:4b",
                        "usage": {},
                        "_provider": "ollama",
                        "_latency_ms": int(r.elapsed.total_seconds() * 1000),
                    }
        except Exception:
            pass

        from app.core.errors import ExternalAPIError

        raise ExternalAPIError(
            message="No AI provider available. Set OPENROUTER_API_KEY, LLM_API_KEY, or run Ollama locally."
        )

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
            "_provider": provider_name,
            "_latency_ms": int(resp.elapsed.total_seconds() * 1000),
        }
