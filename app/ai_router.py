#!/usr/bin/env python3
"""
Stub AI Router - Intelligent Model-First Provider Swapping
===========================================================
Routes requests to optimal AI provider based on quota, latency, and cost.
For now, a minimal stub that delegates to OpenRouter.
"""

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
async def ai_completions(request: dict[str, Any]):
    """AI completion via optimal provider routing."""
    return {"error": "AI Router not fully configured", "provider": "openrouter"}


@router.post("/ai/chat")
async def ai_chat(request: dict[str, Any]):
    """AI chat endpoint with provider fallback."""
    return {"error": "AI Router not fully configured", "provider": "openrouter"}


@router.get("/ai/providers")
async def list_providers():
    """List available AI providers."""
    return {"providers": list(PROVIDERS.keys())}


@router.get("/ai/models")
async def list_models():
    """List available models by tier."""
    return {"tiers": MODEL_TIERS}
