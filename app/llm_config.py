"""
RMI Unified LLM Configuration
==============================
Single source of truth for all LLM providers and models.
Imported by all RAG/content modules to avoid scattered config.

Provider chain:
  PRIMARY:   DeepSeek v4 Flash (cheap, fast) - HyDE, query expansion, chunking
  CONTENT:   NVIDIA Nemotron 3 Super (free) - reports, briefings, writeups
  ANALYSIS:  DeepSeek v4 Pro (promo pricing) - agentic investigation
  FALLBACK:  OpenRouter (auto-routes to best available free model)
  SNAPPY:    Poolside Laguna S 2.1 (free) via OpenRouter - fast short chat answers
  LONG:      NVIDIA Nemotron 3 Ultra 550B (free) via OpenRouter - longer/reasoning answers
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Key resolution ────────────────────────────────────────────────
# DeepSeek key may be stored as base64 (Hermes tooling redacts plain keys)
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")

if os.getenv("LLM_API_KEY_B64"):
    with contextlib.suppress(Exception):
        os.environ["LLM_API_KEY"] = base64.b64decode(os.getenv("LLM_API_KEY_B64") or "").decode()

LLM_API_KEY = os.getenv("LLM_API_KEY", OPENROUTER_KEY)
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")  # optional, for direct NVIDIA access
# OpenRouter backup account (free tier for community chat) — falls back to the
# primary OPENROUTER_KEY if the backup key is not provisioned.
OPENROUTER_BACKUP_API_KEY = os.getenv("OPENROUTER_BACKUP_API_KEY") or OPENROUTER_KEY

# Hugging Face Inference API (free hosted tier — community chat fallback).
# Reads HUGGINGFACE_API_KEY first, then HUGGINGFACE_TOKEN (legacy name used by
# the embedding providers). Provisioned from secrets manager (HF token).
# With no key the free anonymous tier is used (rate-limited; 401s when gated).
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY", "") or os.getenv("HUGGINGFACE_TOKEN", "")
# Default model: free hosted DeepSeek-R1-Distill-Qwen-7B — reasoning/analysis.
HF_MODEL = os.getenv("RMI_HF_MODEL", "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B")
# The legacy api-inference.huggingface.co host was deprecated by HF (no longer
# resolves); the Inference Providers router is the canonical free OpenAI-compatible
# endpoint and matches the OpenAI chat-completions shape used everywhere here.
HF_INFERENCE_BASE = os.getenv("HF_INFERENCE_BASE", "https://router.huggingface.co/v1/chat/completions")

# ── Base URLs ──────────────────────────────────────────────────────
DEEPSEEK_BASE = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# ── Models ─────────────────────────────────────────────────────────
# Content generation: market briefings, scam reports, investigation writeups
CONTENT_MODEL = os.getenv(
    "RMI_CONTENT_MODEL",
    "nvidia/nemotron-3-super-120b-a12b:free",  # 1M context, 120B MoE, FREE
)

# Fast/cheap tasks: HyDE, query expansion, contextual chunking
FAST_MODEL = os.getenv("RMI_FAST_MODEL", "deepseek-v4-flash")

# Deep analysis: agentic investigation, multi-hop reasoning
ANALYSIS_MODEL = os.getenv("RAG_ANALYSIS_MODEL", "deepseek-v4-pro")

# Legacy compat
AI_MODEL = os.getenv("LLM_MODEL", FAST_MODEL)
AI_BASE = os.getenv("LLM_BASE_URL", f"{DEEPSEEK_BASE}/chat/completions")
if not LLM_API_KEY or not os.getenv("LLM_API_KEY"):
    AI_BASE = f"{OPENROUTER_BASE}/chat/completions"


def get_content_config() -> dict[str, Any]:
    """Get config for content generation (Nemotron 3 Super primary)."""
    return {
        "model": CONTENT_MODEL,
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_KEY,
        "max_tokens": 4096,
        "temperature": 0.7,
    }


def get_fast_config() -> dict[str, Any]:
    """Get config for fast/cheap tasks (DeepSeek Flash primary)."""
    if LLM_API_KEY and DEEPSEEK_BASE:
        return {
            "model": FAST_MODEL,
            "base_url": f"{DEEPSEEK_BASE}/chat/completions",
            "api_key": LLM_API_KEY,
            "max_tokens": 2048,
            "temperature": 0.3,
        }
    return get_content_config()  # fallback to OpenRouter


def get_analysis_config() -> dict[str, Any]:
    """Get config for deep analysis (DeepSeek Pro primary)."""
    if LLM_API_KEY and DEEPSEEK_BASE:
        return {
            "model": ANALYSIS_MODEL,
            "base_url": f"{DEEPSEEK_BASE}/chat/completions",
            "api_key": LLM_API_KEY,
            "max_tokens": 8192,
            "temperature": 0.5,
        }
    return get_content_config()


def get_snappy_config() -> dict[str, Any]:
    """Config for fast short chat answers (Poolside Laguna S 2.1, FREE via OpenRouter)."""
    return {
        "model": "poolside/laguna-s-2.1:free",
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 256,
        "temperature": 0.4,
    }


def get_long_config() -> dict[str, Any]:
    """Config for longer/deep answers (NVIDIA Nemotron 3 Ultra 550B, FREE via OpenRouter)."""
    return {
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 1024,
        "temperature": 0.7,
    }


def get_cheap_fallback_config() -> dict[str, Any]:
    """Bottom-of-the-barrel PAID fallback when the free tiers rate-limit.

    Uses the ``:lowest`` modifier so OpenRouter routes to the cheapest host.
    ~$0.05-0.07 / 1M tokens. Ordered cheapest-first; swap the model here to
    change the fallback (qwen-2.5-7b, mistral-7b, llama-3-8b are all viable).
    """
    return {
        "model": "qwen/qwen-2.5-7b-instruct:lowest",
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 512,
        "temperature": 0.5,
    }


def get_hf_config() -> dict[str, Any]:
    """Config for the Hugging Face Inference API free tier.

    ``deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`` — free, hosted (NOT local),
    reasoning/analysis model used as a community-chat fallback. Same OpenAI
    chat-completions shape as every other tier so callers reuse one code path
    (e.g. ``_ask_llm_once`` in bot_api, ``_post_chat`` in ai_router). Fail-open:
    callers treat a missing key or any HTTP error as ``""``.

    Env is read at CALL time (not import time) so the key can be rotated and
    tests can inject a token via monkeypatch.
    """
    return {
        "model": os.getenv("RMI_HF_MODEL", HF_MODEL),
        "base_url": os.getenv("HF_INFERENCE_BASE", HF_INFERENCE_BASE),
        "api_key": os.getenv("HUGGINGFACE_API_KEY", "") or os.getenv("HUGGINGFACE_TOKEN", "") or HUGGINGFACE_API_KEY,
        "max_tokens": 1024,
        "temperature": 0.3,
    }


async def chat_with_fallback(
    messages: list[dict[str, str]],
    tier: str,
    max_tokens: int | None = None,
    model_override: str | None = None,
) -> str:
    """Chat via the free OpenRouter tier, degrading to the cheap PAID model.

    ``tier`` selects the free provider: ``"snappy"`` (poolside/laguna-s-2.1:free)
    or ``"long"`` (nvidia/nemotron-3-ultra-550b-a55b:free). On ANY failure —
    429 rate-limit, endpoint-not-found, timeout, other non-200, empty answer —
    the call is retried ONCE with the paid ``:lowest`` fallback
    (``get_cheap_fallback_config()``, qwen-2.5-7b), so chat never dies on a
    free-tier rate-limit. Returns ``""`` when both attempts fail — callers
    fail open. NEVER raises and never logs the API key.

    If *model_override* is provided the selected config's ``model`` key is
    replaced with it, enabling A/B model comparisons without changing tiers.
    """
    if tier == "snappy":
        config = get_snappy_config()
    elif tier == "long":
        config = get_long_config()
    else:
        return ""  # unknown tier — fail open, no request

    if model_override is not None:
        config = {**config, "model": model_override}

    answer = await _chat_once(config, messages, max_tokens)
    if answer:
        return answer
    answer = await _chat_once(get_cheap_fallback_config(), messages, max_tokens)
    if answer:
        logger.warning("free tier failed (%s) — answered via paid fallback", tier)
    return answer if answer else ""


async def _chat_once(
    config: dict[str, Any],
    messages: list[dict[str, str]],
    max_tokens: int | None,
) -> str:
    """One OpenAI-compatible chat POST to ``config``. Returns stripped content
    or ``""``. Never raises."""
    import httpx

    api_key = str(config.get("api_key") or "")
    base_url = str(config.get("base_url") or "")
    model = str(config.get("model") or "")
    if not api_key or not base_url or not model or not messages:
        return ""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://rugmunch.io",
        "X-Title": "RMI Chat Fallback",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else int(config.get("max_tokens") or 512),
        "temperature": float(config.get("temperature") or 0.5),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0, read=10.0, write=10.0)) as client:
            resp = await asyncio.wait_for(
                client.post(base_url, headers=headers, json=payload),
                timeout=10.0,
            )
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError):
        return ""
    if resp.status_code != 200:
        return ""
    try:
        raw = resp.json()
    except ValueError:
        return ""
    if not isinstance(raw, dict):
        return ""
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if not isinstance(content, str):
        return ""
    content = content.strip()
    return content if content else ""


async def chat_with_hf_fallback(
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
    timeout: float = 60.0,
) -> str:
    """One chat completion via the FREE Hugging Face Inference API tier.

    Bounded + fail-open: returns ``""`` on ANY failure (no key, non-200,
    rate-limit 429/402/503, timeout, empty/degenerate answer). Never raises
    and never logs the key. Reasoning models (DeepSeek-R1) wrap their chain of
    thought in ``<think>`` tags — those are stripped so callers get the final
    answer only.
    """
    from app.core.ai_guardrails import strip_think_tags

    cfg = get_hf_config()
    api_key = str(cfg.get("api_key") or "")
    base_url = str(cfg.get("base_url") or "")
    model = str(cfg.get("model") or "")
    if not base_url or not model or not messages:
        return ""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers["HTTP-Referer"] = "https://rugmunch.io"
    headers["X-Title"] = "RMI HF Chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else int(cfg.get("max_tokens") or 1024),
        "temperature": float(cfg.get("temperature") or 0.3),
    }
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            resp = await client.post(base_url, headers=headers, json=payload)
        if resp.status_code != 200:
            logger.debug("hf_chat_non_200", status=resp.status_code, body=resp.text[:120])
            return ""
        raw = resp.json()
        content = raw["choices"][0]["message"]["content"]
    except (httpx.HTTPError, OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        return ""
    if not isinstance(content, str):
        return ""
    cleaned = strip_think_tags(content).strip()
    return cleaned if cleaned else ""


async def generate_content(
    prompt: str,
    system: str = "You are a crypto intelligence analyst. Be concise, data-driven, and trader-focused.",
    max_tokens: int = 2048,
    temperature: float = 0.7,
) -> str:
    """
    Generate content using Nemotron 3 Super (free, 1M context).
    Auto-falls back to DeepSeek Flash if OpenRouter unavailable.
    """
    import httpx

    config = get_content_config()

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://rugmunch.io",
        "X-Title": "RMI Content Generator",
    }

    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(config["base_url"], headers=headers, json=payload)

        if resp.status_code == 429:
            # Rate limited - try fast config
            logger.warning("Nemotron rate-limited, falling back to DeepSeek Flash")
            fast_cfg = get_fast_config()
            payload["model"] = fast_cfg["model"]
            headers["Authorization"] = f"Bearer {fast_cfg['api_key']}"
            resp = await client.post(fast_cfg["base_url"], headers=headers, json=payload)

        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def generate_briefing(topic: str, context: str = "") -> str:
    """Generate a crypto market briefing."""
    prompt = f"Write a concise crypto market briefing about: {topic}"
    if context:
        prompt += f"\n\nContext:\n{context}"
    prompt += "\n\nKeep it punchy, data-driven, and trader-focused. 3-5 sentences max. No intro."
    return await generate_content(prompt, max_tokens=512, temperature=0.6)


async def generate_scam_report(findings: dict[str, Any]) -> str:
    """Generate a structured scam investigation report."""
    import json

    prompt = f"""Write a structured scam investigation report based on these findings:

{json.dumps(findings, indent=2)}

Format:
1. EXECUTIVE SUMMARY (1-2 sentences)
2. TOKEN DETAILS (name, chain, deployer, age)
3. SCAM INDICATORS (list specific patterns found)
4. RISK ASSESSMENT (critical/high/medium/low with confidence)
5. RECOMMENDATION (avoid/caution/monitor)
"""
    return await generate_content(
        prompt,
        system="You are a blockchain security investigator. Be precise, cite specific on-chain evidence.",
        max_tokens=2048,
        temperature=0.4,
    )
