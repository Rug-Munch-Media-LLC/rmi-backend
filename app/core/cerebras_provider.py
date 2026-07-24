"""Cerebras provider - GPT-OSS-120B, fastest inference on Earth (9ms).
Free tier: 14,400 req/day, 1M tokens/day."""

from __future__ import annotations
import os

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)
CEREBRAS_KEY = os.getenv("CEREBRAS_API_KEY", "")
BASE = "https://api.cerebras.ai/v1"

async def cerebras_chat(
    prompt: str, system: str | None = None, temperature: float = 0.7, max_tokens: int = 1024
) -> dict | None:
    """GPT-OSS-120B via Cerebras - 9ms latency. Use for real-time, latency-sensitive tasks."""
    if not CEREBRAS_KEY:
        return None
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{BASE}/chat/completions",
                json={
                    "model": "gpt-oss-120b",
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                headers={"Authorization": f"Bearer {CEREBRAS_KEY}"},
            )
            if r.status_code == 200:
                d = r.json()
                choice = d["choices"][0]["message"]
                content = choice.get("content") or choice.get("reasoning", "")
                return {
                    "response": content.strip(),
                    "model": "gpt-oss-120b",
                    "tokens": d.get("usage", {}).get("total_tokens", 0),
                    "latency_ms": round(d.get("time_info", {}).get("total_time", 0) * 1000),
                    "provider": "cerebras",
                }
    except Exception as e:
        logger.warning(f"Cerebras failed: {e}")
    return None
