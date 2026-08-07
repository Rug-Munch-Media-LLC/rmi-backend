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
        ("meta-llama/llama-3.3-70b-instruct", "groq", 0.1, 0.0),  # 100ms, free
    ],
    TaskType.CHEAP: [
        ("qwen2.5-coder:7b", "ollama", 2.0, 0.0),  # 2s, free
        ("mistralai/mistral-small", "openrouter", 0.2, 0.0),  # 200ms, free via OpenRouter
    ],
    TaskType.COMPLEX: [
        ("deepseek/deepseek-v4-pro", "openrouter", 0.5, 0.55),  # 500ms, $0.55/1M input
        ("deepseek/deepseek-v4-flash", "openrouter", 0.3, 0.14),  # 300ms, cheap (fallback)
    ],
    TaskType.BULK: [
        ("deepseek/deepseek-v4-flash", "openrouter", 0.3, 0.14),  # 300ms, cheap
        ("mistralai/mistral-small", "openrouter", 0.2, 0.0),  # 200ms, free
    ],
    TaskType.VISION: [
        ("google/gemini-2.5-flash", "openrouter", 0.3, 0.0),  # 300ms, free tier via OR
    ],
    TaskType.EMBED: [
        ("mistralai/mistral-embed", "openrouter", 0.1, 0.0),  # 100ms, free
        ("bge-m3", "infinity", 2.0, 0.0),  # 2s, local
    ],
    TaskType.CODE: [
        ("deepseek/deepseek-v4-flash", "openrouter", 0.3, 0.14),  # 300ms, cheap
        ("qwen2.5-coder:7b", "ollama", 2.0, 0.0),  # 2s, free
        ("mistralai/mistral-small", "openrouter", 0.2, 0.0),  # 200ms, free
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
    """Call a specific provider — routes through LiteLLM when possible.
    Returns dict or None. Gracefully degrades.

    Provider routing:
      - ollama → LiteLLM (qwen-chat on port 4000) or direct Ollama fallback
      - deepseek/cerebras/groq/mistral/gemini → via ai_router.chat_completion
    """
    import httpx

    try:
        if provider == "ollama":
            # Route local inference through LiteLLM for unified routing
            from app.ai_router import chat_completion

            result = await chat_completion(
                messages=[{"role": "user", "content": prompt}],
                task_type="chat",  # LiteLLM routes chat → qwen-chat
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2048),
            )
            if result and "content" in result:
                return {
                    "response": result["content"],
                    "model": result.get("model", model),
                    "provider": result.get("_provider", "litellm"),
                }
            # LiteLLM down — try direct Ollama fallback
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(
                    "http://localhost:11434/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False},
                )
                if r.status_code == 200:
                    return {"response": r.json()["response"], "model": model, "provider": "ollama-direct"}
        elif provider == "deepseek":
            from app.ai_router import chat_completion

            result = await chat_completion(
                messages=[{"role": "user", "content": prompt}],
                task_type="complex",
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2048),
            )
            if result and "content" in result:
                return {"response": result["content"], "model": result.get("model", model), "provider": "deepseek"}
        elif provider == "mistral":
            from app.ai_router import chat_completion

            result = await chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2048),
            )
            if result and "content" in result:
                return {"response": result["content"], "model": result.get("model", model), "provider": "mistral"}
        elif provider == "gemini":
            from app.ai_router import chat_completion

            result = await chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model="google/gemini-2.5-flash",
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2048),
            )
            if result and "content" in result:
                return {"response": result["content"], "model": "gemini-2.5-flash", "provider": "gemini"}
            # Fallback to local via LiteLLM
            result = await chat_completion(
                messages=[{"role": "user", "content": prompt}],
                task_type="chat",
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2048),
            )
            if result and "content" in result:
                return {"response": result["content"], "model": "qwen-chat", "provider": "litellm-fallback"}
        elif provider in ("cerebras", "groq"):
            from app.ai_router import chat_completion

            result = await chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2048),
            )
            if result and "content" in result:
                return {"response": result["content"], "model": model, "provider": provider}
    except Exception:  # noqa: S110 — intentional best-effort
        pass
    return None
