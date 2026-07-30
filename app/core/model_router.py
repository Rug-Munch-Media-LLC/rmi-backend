"""Intelligent Model Router - auto-routes to best provider by task type, cost, latency.
Priority: real-time → Cerebras (9ms), cheap → Ollama ($0), complex → DeepSeek, bulk → Mistral."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TaskType(Enum):
    FAST = "fast"  # < 100ms needed - Cerebras, Groq
    CHEAP = "cheap"  # cost-sensitive - Ollama, Mistral free tier
    COMPLEX = "complex"  # reasoning needed - DeepSeek V4 Pro
    BULK = "bulk"  # high volume - Mistral Small 4
    VISION = "vision"  # image understanding - Gemini
    EMBED = "embed"  # embeddings - Mistral Embed
    CODE = "code"  # code generation - DeepSeek, qwen2.5-coder


ROUTING_TABLE = {
    TaskType.FAST: [
        ("gpt-oss-120b", "cerebras", 0.009, 0.0),  # 9ms, free
        ("llama-3.3-70b", "groq", 0.1, 0.0),  # 100ms, free
    ],
    TaskType.CHEAP: [
        ("qwen2.5-coder:7b", "ollama", 2.0, 0.0),  # 2s, free
        ("mistral-small-latest", "mistral", 0.2, 0.0),  # 200ms, free
    ],
    TaskType.COMPLEX: [
        ("deepseek-v4-pro", "deepseek", 0.5, 0.55),  # 500ms, $0.55/1M input
        ("mistral-medium-latest", "mistral", 0.3, 0.0),  # 300ms, free
    ],
    TaskType.BULK: [
        ("mistral-small-latest", "mistral", 0.2, 0.0),  # 200ms, free, 2M TPM
        ("deepseek-v4-flash", "deepseek", 0.3, 0.14),  # 300ms, cheap
    ],
    TaskType.VISION: [
        ("gemini-2.5-flash", "gemini", 0.3, 0.0),  # 300ms, free tier
    ],
    TaskType.EMBED: [
        ("mistral-embed", "mistral", 0.1, 0.0),  # 100ms, 20M TPM free
        ("bge-m3", "ollama", 2.0, 0.0),  # 2s, local
    ],
    TaskType.CODE: [
        ("deepseek-v4-flash", "deepseek", 0.3, 0.14),  # 300ms, cheap
        ("qwen2.5-coder:7b", "ollama", 2.0, 0.0),  # 2s, free
        ("mistral-small-latest", "mistral", 0.2, 0.0),  # 200ms, free
    ],
}


@dataclass
class RoutingDecision:
    model: str
    provider: str
    estimated_latency_ms: float
    cost_per_1m_input: float
    fallback_model: str | None = None
    fallback_provider: str | None = None


def route_task(task_type: TaskType, prefer: str = "fast") -> RoutingDecision:
    """Route a task to the best model. Falls back to next on failure."""
    candidates = ROUTING_TABLE.get(task_type, ROUTING_TABLE[TaskType.FAST])

    if prefer == "cheap":
        candidates = sorted(candidates, key=lambda c: c[3])  # sort by cost
    elif prefer == "fast":
        candidates = sorted(candidates, key=lambda c: c[2])  # sort by latency

    primary = candidates[0]
    fallback = candidates[1] if len(candidates) > 1 else None

    return RoutingDecision(
        model=primary[0],
        provider=primary[1],
        estimated_latency_ms=primary[2] * 1000,
        cost_per_1m_input=primary[3],
        fallback_model=fallback[0] if fallback else None,
        fallback_provider=fallback[1] if fallback else None,
    )


async def smart_route(prompt: str, task_type: str = "fast", prefer: str = "fast", **kwargs) -> dict[str, str] | dict:
    """Auto-route a prompt to the best model. Returns response or falls back."""
    decision = route_task(TaskType(task_type), prefer)

    # Try primary
    result = await _call_provider(decision.provider, decision.model, prompt, **kwargs)
    if result:
        return {**result, "routing": vars(decision), "fallback_used": False}

    # Try fallback
    if decision.fallback_model:
        result = await _call_provider(decision.fallback_provider, decision.fallback_model, prompt, **kwargs)
        if result:
            return {**result, "routing": vars(decision), "fallback_used": True}

    return {"error": "All providers failed", "routing": vars(decision)}


async def _call_provider(provider: str, model: str, prompt: str, **kwargs) -> None | Any | dict:
    """Call a specific provider. Returns dict or None."""
    try:
        if provider == "cerebras":
            from app.core.cerebras_provider import cerebras_chat

            return await cerebras_chat(prompt, **kwargs)
        elif provider == "mistral":
            from app.core.mistral_provider import mistral_chat

            return await mistral_chat(prompt, model=model, **kwargs)
        elif provider == "ollama":
            import httpx

            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(
                    "http://localhost:11434/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False},
                )
                if r.status_code == 200:
                    return {"response": r.json()["response"], "model": model, "provider": "ollama"}
        # DeepSeek, Groq, Gemini handled via existing DataBus providers
    except (AttributeError, ImportError, httpx.HTTPError):
        pass
    return None
