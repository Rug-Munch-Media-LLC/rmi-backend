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
"""

from __future__ import annotations
import base64
import contextlib
import os

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Key resolution ────────────────────────────────────────────────
# DeepSeek key may be stored as base64 (Hermes tooling redacts plain keys)
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")

if os.getenv("LLM_API_KEY_B64"):
    with contextlib.suppress(Exception):
        os.environ["LLM_API_KEY"] = base64.b64decode(os.getenv("LLM_API_KEY_B64")).decode()

LLM_API_KEY = os.getenv("LLM_API_KEY", OPENROUTER_KEY)
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")  # optional, for direct NVIDIA access

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

def get_content_config() -> dict:
    """Get config for content generation (Nemotron 3 Super primary)."""
    return {
        "model": CONTENT_MODEL,
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_KEY,
        "max_tokens": 4096,
        "temperature": 0.7,
    }

def get_fast_config() -> dict:
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

def get_analysis_config() -> dict:
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

async def generate_scam_report(findings: dict) -> str:
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
