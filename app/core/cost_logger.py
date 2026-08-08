"""ClickHouse-backed cost tracking for AI model calls.

Usage:
    from app.core.cost_logger import log_cost, get_usage

    await log_cost(model="deepseek-v4-flash", tokens_in=100, tokens_out=50, cost_usd=0.0001, latency_ms=300)
"""

from __future__ import annotations
from typing import Any

import os

CLICKHOUSE_HOST = os.getenv("CH_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CH_PORT", "9000"))
CLICKHOUSE_USER = os.getenv("CH_USER", "rmi")
CLICKHOUSE_PASSWORD = os.environ["CH_PASSWORD"]
CLICKHOUSE_DB = "rmi"

MODEL_COSTS = {
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28, "provider": "deepseek"},
    "deepseek-v4-pro": {"input": 0.55, "output": 2.19, "provider": "deepseek"},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "provider": "gemini"},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "provider": "gemini"},
    "mistral-large": {"input": 2.00, "output": 6.00, "provider": "mistral"},
    "mistral-small": {"input": 0.20, "output": 0.60, "provider": "mistral"},
}

# In-memory buffer for batching writes to ClickHouse
_buffer: list[tuple] = []
_BUFFER_FLUSH_INTERVAL = 30  # seconds
_last_flush = 0


def _compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    costs = MODEL_COSTS.get(model, {"input": 0, "output": 0})
    input_cost = (tokens_in / 1_000_000) * costs["input"]
    output_cost = (tokens_out / 1_000_000) * costs["output"]
    return round(input_cost + output_cost, 8)


def log_cost_sync(
    model_name: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float | None = None,
    latency_ms: float = 0,
    agent_name: str = "unknown",
    status: str = "success",
) -> None:
    """Synchronous cost logging to ClickHouse.

    If cost_usd is None, it's computed from MODEL_COSTS.
    """
    global _last_flush
    cost = cost_usd if cost_usd is not None else _compute_cost(model_name, tokens_in, tokens_out)

    try:
        from clickhouse_driver import Client

        client = Client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            user=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DB,
            connect_timeout=5,
            send_receive_timeout=10,
        )
        client.execute(
            "INSERT INTO cost_attribution (model_name, agent_name, tokens_in, tokens_out, cost_usd, latency_ms, status) VALUES",
            [(model_name, agent_name, tokens_in, tokens_out, cost, int(latency_ms), status)],
        )
        client.disconnect()
    except (ConnectionError, OSError):
        _buffer.append((model_name, agent_name, tokens_in, tokens_out, cost, int(latency_ms), status))


async def log_cost(
    model_name: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float | None = None,
    latency_ms: float = 0,
    agent_name: str = "unknown",
    status: str = "success",
) -> None:
    """Async-friendly cost logging. Falls back to sync for simplicity."""
    log_cost_sync(model_name, tokens_in, tokens_out, cost_usd, latency_ms, agent_name, status)


async def get_usage(limit: int = 50) -> dict:
    """Get recent cost usage from ClickHouse."""
    try:
        from clickhouse_driver import Client

        client = Client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            user=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DB,
            connect_timeout=5,
            send_receive_timeout=10,
        )
        rows = client.execute(
            "SELECT timestamp, model_name, agent_name, tokens_in, tokens_out, cost_usd, latency_ms, status "
            "FROM cost_attribution ORDER BY timestamp DESC LIMIT %(limit)s",
            {"limit": limit},
        )
        client.disconnect()
    except (ConnectionError, OSError):
        return {"total_cost_usd": 0, "total_tokens": 0, "entries": 0, "log": []}

    log = [
        {
            "timestamp": str(r[0]),
            "model": r[1],
            "agent": r[2],
            "input_tokens": r[3],
            "output_tokens": r[4],
            "cost_usd": r[5],
            "latency_ms": r[6],
            "status": r[7],
        }
        for r in rows
    ]
    total_cost = sum(e["cost_usd"] for e in log)
    total_tokens = sum(e["input_tokens"] + e["output_tokens"] for e in log)
    return {
        "total_cost_usd": round(total_cost, 6),
        "total_tokens": total_tokens,
        "entries": len(log),
        "log": log,
    }


async def get_daily_summary(days: int = 7) -> dict:
    """Get cost breakdown by day and model."""
    try:
        from clickhouse_driver import Client

        client = Client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            user=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DB,
            connect_timeout=5,
            send_receive_timeout=10,
        )
        by_day = client.execute(
            "SELECT toDate(timestamp) AS day, model_name, sum(cost_usd), sum(tokens_in + tokens_out), count() "
            "FROM cost_attribution "
            "WHERE timestamp >= now() - INTERVAL %(days)s DAY "
            "GROUP BY day, model_name ORDER BY day DESC",
            {"days": days},
        )
        total = client.execute(
            "SELECT sum(cost_usd), sum(tokens_in + tokens_out), count() "
            "FROM cost_attribution "
            "WHERE timestamp >= now() - INTERVAL %(days)s DAY",
            {"days": days},
        )
        client.disconnect()
    except (ConnectionError, OSError):
        return {"days": days, "total_cost": 0, "total_tokens": 0, "total_calls": 0, "by_day": []}

    return {
        "days": days,
        "total_cost": round(total[0][0], 6) if total else 0,
        "total_tokens": total[0][1] if total else 0,
        "total_calls": total[0][2] if total else 0,
        "by_day": [
            {
                "date": str(r[0]),
                "model": r[1],
                "cost": round(r[2], 6),
                "tokens": r[3],
                "calls": r[4],
            }
            for r in by_day
        ],
    }
