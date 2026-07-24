"""
Model Registry - Provider API Callers & AI Routing
====================================================
Direct API callers for OpenRouter, Groq, Mistral and the ai_call router.
"""

from __future__ import annotations
import os

import httpx

from app.core.logging import get_logger

from .models import MODELS
from .tracking import _can_use, _track_usage

logger = get_logger("model_registry")

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY", "")
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

async def _call_openrouter(
    model_id: str, system: str, user: str, max_tokens: int = 1000, temperature: float = 0.5
) -> str:
    """Call OpenRouter API."""
    if not OPENROUTER_KEY:
        return ""

    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(
                OR_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://rugmunch.io",
                    "X-Title": "RugCharts AI",
                },
                json={
                    "model": model_id,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if r.status_code == 200:
                resp = r.json()
                usage = resp.get("usage", {})
                _track_usage(model_id, usage.get("total_tokens", 0))
                return resp["choices"][0]["message"]["content"]
            else:
                logger.warning(f"OpenRouter {model_id}: {r.status_code}")
                return ""
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"OpenRouter error {model_id}: {e}")
        return ""

async def _call_groq(
    model_id: str, system: str, user: str, max_tokens: int = 1000, temperature: float = 0.5
) -> str:
    """Call Groq API (free tier)."""
    if not GROQ_KEY:
        return ""

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={
                    "model": model_id,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            else:
                logger.warning(f"Groq {model_id}: {r.status_code} {r.text[:200]}")
                return ""
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Groq error: {e}")
        return ""

async def _call_mistral(
    model_id: str, system: str, user: str, max_tokens: int = 1000, temperature: float = 0.5
) -> str:
    """Call Mistral API (free tier)."""
    if not MISTRAL_KEY:
        return ""

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                MISTRAL_URL,
                headers={
                    "Authorization": f"Bearer {MISTRAL_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_id,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if r.status_code == 200:
                _track_usage(model_id, max_tokens)
                return r.json()["choices"][0]["message"]["content"]
            else:
                logger.warning(f"Mistral {model_id}: {r.status_code}")
                return ""
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Mistral error: {e}")
        return ""

async def ai_call(
    task_type: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.5,
) -> str:
    """THE method. Call the best free model for a task type.

    Routes to: research, writing, coding, review, fast.
    Falls back: primary -> fallback -> groq -> mistral -> any available.
    Three providers: OpenRouter, Groq, Mistral. Zero cost.
    """
    if task_type not in MODELS:
        task_type = "fast"

    config = MODELS[task_type]
    tiers = ["primary", "fallback", "groq"]

    for tier in tiers:
        if tier not in config:
            continue
        model = config[tier]
        if not _can_use(model):
            continue

        if model["provider"] == "openrouter":
            result = await _call_openrouter(
                model["id"], system_prompt, user_prompt, max_tokens, temperature
            )
        elif model["provider"] == "groq":
            result = await _call_groq(
                model["id"], system_prompt, user_prompt, max_tokens, temperature
            )
        elif model["provider"] == "mistral":
            result = await _call_mistral(
                model["id"], system_prompt, user_prompt, max_tokens, temperature
            )
        else:
            continue

        if result:
            return result

    # Extended fallback: try Mistral models
    mistral_tasks = ["mistral_fast", "mistral_write", "mistral_code"]
    for mt in mistral_tasks:
        if mt == task_type:
            continue
        mconfig = MODELS.get(mt, {})
        for tier in ["primary", "fallback"]:
            if tier not in mconfig:
                continue
            model = mconfig[tier]
            if _can_use(model):
                result = await _call_mistral(
                    model["id"], system_prompt, user_prompt, max_tokens, temperature
                )
                if result:
                    return result

    # Last resort: try any available free model
    for backup_type in ["fast", "writing", "writing_groq"]:
        if backup_type == task_type:
            continue
        backup_config = MODELS[backup_type]
        for tier_name in ["primary", "fallback", "groq"]:
            if tier_name in backup_config:
                model = backup_config[tier_name]
                if _can_use(model):
                    if model["provider"] == "openrouter":
                        result = await _call_openrouter(
                            model["id"], system_prompt, user_prompt, max_tokens, temperature
                        )
                    elif model["provider"] == "groq":
                        result = await _call_groq(
                            model["id"], system_prompt, user_prompt, max_tokens, temperature
                        )
                    elif model["provider"] == "mistral":
                        result = await _call_mistral(
                            model["id"], system_prompt, user_prompt, max_tokens, temperature
                        )
                    if result:
                        return result

    return ""
