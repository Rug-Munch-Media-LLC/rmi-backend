"""Intelligent Model Router - auto-routes to best provider by task type, cost, latency.
Priority: fast → DeepSeek Flash (direct API), cheap → Ollama ($0), complex → DeepSeek Pro, bulk → DeepSeek Flash."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TaskType(Enum):
    FAST = "fast"  # < 300ms needed - DeepSeek Flash (direct API), Groq
    CHEAP = "cheap"  # cost-sensitive - Ollama, Mistral free tier
    COMPLEX = "complex"  # reasoning needed - DeepSeek V4 Pro
    BULK = "bulk"  # high volume - Mistral Small 4
    VISION = "vision"  # image understanding - Gemini
    EMBED = "embed"  # embeddings - Mistral Embed
    CODE = "code"  # code generation - DeepSeek, qwen2.5-coder


ROUTING_TABLE = {
    TaskType.FAST: [
        # gpt-oss-120b/cerebras removed 2026-08-14 — free-tier rate limits + OOM'd
        # the backend repeatedly. DeepSeek Flash (direct API) is the reliable primary.
        ("deepseek/deepseek-v4-flash", "openrouter", 0.25, 0.14),  # direct DeepSeek API, reliable
        ("meta-llama/llama-3.3-70b-instruct", "groq", 0.3, 0.0),  # free fallback
    ],
    TaskType.CHEAP: [
        ("deepseek/deepseek-v4-flash", "openrouter", 0.3, 0.14),  # reliable paid-fallback-first
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
        ("bge-m3", "infinity", 2.0, 0.0),  # 2s, local (infinity-rag)
        ("mistralai/mistral-embed", "openrouter", 0.1, 0.0),  # 100ms, free
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


async def smart_route(
    prompt: str, task_type: str = "fast", prefer: str = "fast", **kwargs
) -> dict[str, str] | dict[str, Any]:
    """Auto-route a prompt to the best model. Returns response or falls back."""
    decision = route_task(TaskType(task_type), prefer)

    # Try primary
    result = await _call_provider(decision.provider, decision.model, prompt, **kwargs)
    if result:
        return {**result, "routing": vars(decision), "fallback_used": False}

    # Try fallback
    if decision.fallback_model and decision.fallback_provider:
        result = await _call_provider(decision.fallback_provider, decision.fallback_model, prompt, **kwargs)
        if result:
            return {**result, "routing": vars(decision), "fallback_used": True}

    return {"error": "All providers failed", "routing": vars(decision)}


async def _call_provider(provider: str, model: str, prompt: str, **kwargs) -> Any | dict[str, Any] | None:
    """Call a specific provider — routes through LiteLLM when possible.
    Returns dict or None. Gracefully degrades.

    Provider routing:
      - ollama → remote chat-completion fallback or direct Ollama fallback
      - deepseek/openrouter → direct DeepSeek API (api.deepseek.com)
      - groq/mistral/gemini → via ai_router.chat_completion
    """
    import httpx

    try:
        if provider == "ollama":
            # Route local inference through the remote chat-completion tier
            from app.ai_router import chat_completion

            result = await chat_completion(
                messages=[{"role": "user", "content": prompt}],
                task_type="chat",  # ai_router routes chat → snappy tier
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
        elif provider in ("deepseek", "openrouter"):
            # Direct DeepSeek API (api.deepseek.com) — NOT OpenRouter, NOT Cerebras.
            # canonical config: llm_config.get_fast_config()/get_analysis_config().
            from app import llm_config

            cfg = llm_config.get_analysis_config() if "pro" in model else llm_config.get_fast_config()
            import httpx as _httpx

            async with _httpx.AsyncClient(timeout=90) as _c:
                _r = await _c.post(
                    cfg["base_url"],
                    json={
                        "model": cfg["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": kwargs.get("max_tokens", cfg["max_tokens"]),
                        "temperature": kwargs.get("temperature", cfg["temperature"]),
                    },
                    headers={
                        "Authorization": f"Bearer {cfg['api_key']}",
                        "Content-Type": "application/json",
                    },
                )
                if _r.status_code == 200:
                    _d = _r.json()
                    _msg = _d["choices"][0]["message"]
                    # NEVER substitute reasoning_content: DeepSeek's chain of
                    # thought lives there and must not reach users. content is
                    # the final answer; empty content = degenerate completion.
                    _content = (_msg.get("content") or "").strip()
                    if not _content:
                        return None
                    from app.core.ai_guardrails import strip_think_tags

                    _content = strip_think_tags(_content)
                    return {
                        "response": _content,
                        "model": _d.get("model", cfg["model"]),
                        "provider": "deepseek-direct",
                    }
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
                return {
                    "response": result["content"],
                    "model": result.get("model", "chat-fallback"),
                    "provider": "litellm-fallback",
                }
        elif provider == "groq":
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
