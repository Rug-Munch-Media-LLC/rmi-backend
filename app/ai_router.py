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


def _strip_think_tags(text: str) -> str:
    """Strip <think> tags from model output (Qwen3/DeepSeek R1 reasoning)."""
    import re

    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _prefill_chat(messages: list) -> list:
    """Add pre-fill assistant message to bypass Qwen3 thinking for chat."""
    last = messages[-1]
    if last.get("role") == "user":
        msg = last["content"].lower()
        if any(w in msg for w in ["what", "how", "explain", "tell", "describe"]):
            messages.append({"role": "assistant", "content": "Here"})
        else:
            messages.append({"role": "assistant", "content": "Sure"})
    return messages


# ── Task → Model routing map ──
# LiteLLM model_group_alias → actual model:
#
# Task routing table:
#   "chat"      → model="chat"     → qwen-chat       (fast, creative, 11 tok/s)
#   "simple"    → model="fast"     → qwen-chat       (greetings, basic Q&A)
#   "analysis"  → model="analysis" → deepseek-analysis (deep, reasoning, 4 tok/s)
#   "complex"   → model="deep"     → deepseek-analysis (RAG, scoring, forensics)
#   "chart"     → model="deep"     → deepseek-analysis (needs technical analysis)
#   "contract"  → model="deep"     → deepseek-analysis (needs security knowledge)
#   "code"      → model="deep"     → deepseek-analysis (code generation/analysis)
#   "bulk"      → model="fast"     → qwen-chat       (high-volume, simple tasks)
#
# Default (no task_type, no model): "chat" → qwen-chat
TASK_MODEL_MAP = {
    "chat": "chat",  # LiteLLM model_group_alias → qwen-chat
    "analysis": "analysis",  # LiteLLM model_group_alias → deepseek-analysis
    "simple": "fast",  # LiteLLM model_group_alias → qwen-chat (fast alias)
    "complex": "deep",  # LiteLLM model_group_alias → deepseek-analysis (deep alias)
    "code": "deep",  # Complex tasks → deep reasoning model
    "bulk": "fast",  # High-volume tasks → fast model
}

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://127.0.0.1:4000/v1")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", os.getenv("LITELLM_MASTER_KEY", "sk-litellm"))


async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    task_type: str | None = None,
    tier: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Call the LLM API via LiteLLM proxy with task-based routing.

    Routes through LiteLLM on port 4000 which handles provider selection,
    usage-based routing, and fallback. Falls back to local Ollama if
    LiteLLM is unavailable.

    Task routing (via task_type param):
      - "chat" / "simple"  → model="chat" (LiteLLM group → qwen-chat)
      - "analysis" / "complex" / "code" → model="analysis"/"deep" (→ deepseek-analysis)
      - None → uses explicit model param if provided, otherwise "chat"

    Args:
        messages: Chat messages in OpenAI format.
        model: Explicit model name (overrides task_type routing).
        task_type: Task category for auto-routing (chat/analysis/simple/complex/code/bulk).
        tier: Deprecated, kept for API compatibility.
        temperature: Sampling temperature (0.0-2.0).
        max_tokens: Maximum tokens in response.

    Returns:
        Dict with id, content, choices, model, usage, _provider, _latency_ms.
    """
    import httpx

    # Resolve model: explicit model > task_type routing > default
    if model:
        resolved_model = model
    elif task_type and task_type in TASK_MODEL_MAP:
        resolved_model = TASK_MODEL_MAP[task_type]
    else:
        resolved_model = "chat"  # default to qwen-chat

    # ── GUARDRAILS: Sanitize last user message before sending ──
    from app.core.ai_guardrails import sanitize_input as _guard_input, sanitize_output as _guard_output

    messages_safe = list(messages)
    for i in range(len(messages_safe) - 1, -1, -1):
        if messages_safe[i].get("role") == "user":
            original = messages_safe[i].get("content", "")
            cleaned = _guard_input(original)
            if cleaned.startswith("[BLOCKED"):
                import logging

                logging.getLogger("ai_router").warning("guardrails_blocked_input", model=resolved_model, reason=cleaned)
                return {
                    "id": "guardrails-blocked",
                    "content": cleaned,
                    "choices": [{"message": {"content": cleaned}}],
                    "model": "guardrails",
                    "usage": {},
                    "_provider": "guardrails",
                    "_latency_ms": 0,
                }
            messages_safe[i]["content"] = cleaned
            break

    # ── Primary: LiteLLM proxy ──
    litellm_url = f"{LITELLM_BASE_URL}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                litellm_url,
                json={
                    "model": resolved_model,
                    "messages": messages_safe,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                headers={
                    "Authorization": f"Bearer {LITELLM_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                content = ""
                if choices and "message" in choices[0]:
                    content = _strip_think_tags(choices[0]["message"].get("content", ""))
                # ── GUARDRAILS: Sanitize output ──
                content = _guard_output(content)
                return {
                    "id": data.get("id", "chat-unknown"),
                    "content": content,
                    "choices": choices,
                    "model": data.get("model", resolved_model),
                    "usage": data.get("usage", {}),
                    "_provider": "litellm",
                    "_latency_ms": int(resp.elapsed.total_seconds() * 1000),
                    "_routed_model": resolved_model,
                }
            # LiteLLM returned non-200 — log and fall through to fallback
            import logging

            logger = logging.getLogger("ai_router")
            logger.warning(
                "litellm_non_200",
                status=resp.status_code,
                body=resp.text[:300],
                model=resolved_model,
            )
    except Exception as e:
        import logging

        logger = logging.getLogger("ai_router")
        logger.warning("litellm_unreachable", error=str(e))

    # ── Fallback 1: Try OpenRouter if configured ──
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    if openrouter_key:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json={
                        "model": "deepseek/deepseek-v4-flash",
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    headers={
                        "Authorization": f"Bearer {openrouter_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    content = ""
                    if choices and "message" in choices[0]:
                        content = _strip_think_tags(choices[0]["message"].get("content", ""))
                    # ── GUARDRAILS: Sanitize output ──
                    content = _guard_output(content)
                    return {
                        "id": data.get("id", "chat-unknown"),
                        "content": content,
                        "choices": choices,
                        "model": "deepseek/deepseek-v4-flash",
                        "usage": data.get("usage", {}),
                        "_provider": "openrouter-fallback",
                        "_latency_ms": int(resp.elapsed.total_seconds() * 1000),
                    }
        except Exception:
            pass

    # ── Fallback 2: Try DeepSeek direct if configured ──
    deepseek_key = os.getenv("LLM_API_KEY", "")
    if deepseek_key:
        try:
            url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
            ds_model = os.getenv("LLM_MODEL", "deepseek-v4-flash")
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "model": ds_model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    headers={
                        "Authorization": f"Bearer {deepseek_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    content = ""
                    if choices and "message" in choices[0]:
                        content = _strip_think_tags(choices[0]["message"].get("content", ""))
                    # ── GUARDRAILS: Sanitize output ──
                    content = _guard_output(content)
                    return {
                        "id": data.get("id", "chat-unknown"),
                        "content": content,
                        "choices": choices,
                        "model": ds_model,
                        "usage": data.get("usage", {}),
                        "_provider": "deepseek-fallback",
                        "_latency_ms": int(resp.elapsed.total_seconds() * 1000),
                    }
        except Exception:
            pass

    # ── Fallback 3: Try local Ollama as last resort ──
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "qwen3:4b",
                    "prompt": messages[-1]["content"] if messages else "",
                    "stream": False,
                },
            )
            if r.status_code == 200:
                resp_data = r.json()
                resp_text = _strip_think_tags(resp_data.get("response", ""))
                # ── GUARDRAILS: Sanitize output ──
                resp_text = _guard_output(resp_text)
                return {
                    "id": "ollama-local",
                    "content": resp_text,
                    "choices": [{"message": {"content": resp_text}}],
                    "model": "qwen3:4b",
                    "usage": {},
                    "_provider": "ollama-fallback",
                    "_latency_ms": int(r.elapsed.total_seconds() * 1000),
                }
    except Exception:
        pass

    from app.core.errors import ExternalAPIError

    raise ExternalAPIError(
        message="No AI provider available. Ensure LiteLLM is running on port 4000, "
        "or set OPENROUTER_API_KEY / LLM_API_KEY as fallback."
    )
