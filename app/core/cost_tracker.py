"""#10 - Cost-Per-Model Tracking. Tracks $/1K tokens per model/provider.
Auto-routes to cheapest model that meets quality threshold."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/costs", tags=["cost-tracking"])

# Cost per 1M tokens (USD) - updated June 2026
MODEL_COSTS = {
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28, "provider": "deepseek"},
    "deepseek-v4-pro": {"input": 0.55, "output": 2.19, "provider": "deepseek"},
    # Gemini pricing (paid tier, per 1M tokens)
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "provider": "gemini"},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "provider": "gemini"},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00, "provider": "gemini"},
    "mistral-small-latest": {
        "input": 0.0,
        "output": 0.0,
        "provider": "mistral",
        "note": "Free tier - 1B tokens/mo",
    },
    "mistral-medium-latest": {
        "input": 0.0,
        "output": 0.0,
        "provider": "mistral",
        "note": "Free tier - use sparingly",
    },
    "mistral-embed": {
        "input": 0.0,
        "output": 0.0,
        "provider": "mistral",
        "note": "Free tier - state of art embeddings",
    },
    "mistral-large": {"input": 2.00, "output": 6.00, "provider": "mistral"},
    "mistral-small": {"input": 0.20, "output": 0.60, "provider": "mistral"},
    "qwen2.5-coder:7b": {"input": 0.0, "output": 0.0, "provider": "ollama"},
    "gpt-oss-120b": {
        "input": 0.0,
        "output": 0.0,
        "provider": "cerebras",
        "note": "Free tier - 14.4K req/day, 9ms latency",
    },
    "mistral:7b": {"input": 0.0, "output": 0.0, "provider": "ollama"},
    "bge-m3": {"input": 0.0, "output": 0.0, "provider": "infinity"},
}

_usage_log: list[dict] = []


def log_usage(model: str, input_tokens: int, output_tokens: int, latency_ms: float) -> None:
    """Log model usage for cost tracking."""
    costs = MODEL_COSTS.get(model, {"input": 0, "output": 0, "provider": "unknown"})
    input_cost = (input_tokens / 1_000_000) * costs["input"]
    output_cost = (output_tokens / 1_000_000) * costs["output"]
    total_cost = input_cost + output_cost

    _usage_log.append(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "model": model,
            "provider": costs["provider"],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(total_cost, 6),
            "latency_ms": latency_ms,
        }
    )


def get_cheapest_model(models: list[str]) -> str:
    """Pick the cheapest model from a list that meets quality."""
    best = None
    best_cost = float("inf")
    for m in models:
        c = MODEL_COSTS.get(m, {})
        cost = c.get("output", 1.0)
        if cost < best_cost:
            best_cost = cost
            best = m
    return best or models[0]


@router.get("/rates")
async def get_rates() -> dict:
    """Get current model pricing."""
    return {"models": MODEL_COSTS, "updated": "2026-06-15"}


@router.get("/usage")
async def get_usage(limit: int = Query(50, le=200)) -> dict:
    """Get recent usage log."""
    recent = _usage_log[-limit:]
    total_cost = sum(e["cost_usd"] for e in recent)
    total_tokens = sum(e["input_tokens"] + e["output_tokens"] for e in recent)
    return {
        "total_cost_usd": round(total_cost, 4),
        "total_tokens": total_tokens,
        "entries": len(recent),
        "log": recent,
    }


@router.get("/cheapest")
async def cheapest_for_task(
    quality: str = Query("medium", description="minimum quality tier"),
) -> dict:
    """Get cheapest model for a given quality tier."""
    if quality == "high":
        candidates = ["deepseek-v4-pro", "gemini-2.5-pro", "mistral-large"]
    elif quality == "medium":
        candidates = ["deepseek-v4-flash", "gemini-2.5-flash", "mistral-small", "qwen2.5-coder:7b"]
    else:
        candidates = ["qwen2.5-coder:7b", "mistral:7b", "deepseek-v4-flash"]

    cheapest = get_cheapest_model(candidates)
    return {
        "quality_tier": quality,
        "candidates": candidates,
        "cheapest": cheapest,
        "cost_per_1M_output": MODEL_COSTS.get(cheapest, {}).get("output", "?"),
    }
