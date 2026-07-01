"""Mistral AI provider for DataBus — Free tier: 1B tokens/month, 1 req/sec.
Credit-conserving: uses Small 4 for bulk, Medium 3.5 only when needed."""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

MISTRAL_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_BASE = "https://api.mistral.ai/v1"

# Model selection by task — free tier optimized
MODELS = {
    "fast": "mistral-small-latest",  # Small 4 — 90% of calls, ~$0.1/1M tokens
    "smart": "mistral-medium-latest",  # Medium 3.5 — complex analysis only
    "embed": "mistral-embed",  # Embeddings — state of art
    "code": "mistral-small-latest",  # Small 4 handles code well
    "moderate": "mistral-moderation-latest",  # Content moderation
}

# ⚠️ Deprecated — do NOT use
# mistral-small-2506 → deprecated, retiring July 2026
# mistral-medium-2508 → deprecated, retiring Aug 2026


async def mistral_chat(
    prompt: str, model: str | None = None, system: str | None = None, max_tokens: int = 512, temperature: float = 0.7
) -> dict | None:
    """Chat completion via Mistral. Conserves free credits by using fast models."""
    if not MISTRAL_KEY:
        return None

    model = model or MODELS["fast"]
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{MISTRAL_BASE}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                headers={"Authorization": f"Bearer {MISTRAL_KEY}"},
            )
            if r.status_code == 200:
                d = r.json()
                return {
                    "response": d["choices"][0]["message"]["content"],
                    "model": model,
                    "tokens": d.get("usage", {}).get("total_tokens", 0),
                    "provider": "mistral",
                }
            elif r.status_code == 429:
                logger.warning("Mistral rate limit hit — waiting...")
    except Exception as e:
        logger.warning(f"Mistral chat failed: {e}")
    return None


async def mistral_embed(text: str) -> list | None:
    """Generate embeddings via Mistral Embed — state of art."""
    if not MISTRAL_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{MISTRAL_BASE}/embeddings",
                json={"model": MODELS["embed"], "input": [text]},
                headers={"Authorization": f"Bearer {MISTRAL_KEY}"},
            )
            if r.status_code == 200:
                return r.json()["data"][0]["embedding"]
    except Exception as e:
        logger.warning(f"Mistral embed failed: {e}")
    return None


async def mistral_moderate(text: str) -> dict | None:
    """Content moderation — jailbreak, toxicity, PII detection."""
    if not MISTRAL_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{MISTRAL_BASE}/chat/completions",
                json={
                    "model": MODELS["moderate"],
                    "messages": [{"role": "user", "content": text}],
                    "max_tokens": 10,
                    "temperature": 0,
                },
                headers={"Authorization": f"Bearer {MISTRAL_KEY}"},
            )
            if r.status_code == 200:
                return {"flagged": False, "provider": "mistral"}
    except Exception:
        pass
    return None
