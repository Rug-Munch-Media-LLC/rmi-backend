"""Canonical trending engine client (``rugbrain.trending_fusion/v1``).

rmi-backend owns no ranker. This is the only path to a ranked list: one GET
against the canonical engine. Everything else in ``app/trending`` is a raw
row accessor (ClickHouse / parquet / pumpfun). No local fallback algorithm is
permitted — an outage is a 503, not a second score.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

ENGINE_ID = "rugbrain.trending_fusion/v1"
UNAVAILABLE_SOURCE = "rugbrain.trending_fusion"
DEFAULT_BASE = "http://127.0.0.1:8000"


class TrendingUnavailable(RuntimeError):
    """The canonical engine did not serve a valid ranked payload."""

    def __init__(self, reason: str, source: str = UNAVAILABLE_SOURCE) -> None:
        """Carry the typed reason so routes can emit the refusal envelope."""
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason

    def envelope(self) -> dict[str, str]:
        """Refusal body: ``{"status": "unavailable", "source": ..., "reason": ...}``."""
        return {"status": "unavailable", "source": self.source, "reason": self.reason}


def engine_base() -> str:
    """Base URL of the canonical engine (env ``RUGBRAIN_API``)."""
    return (os.environ.get("RUGBRAIN_API") or DEFAULT_BASE).rstrip("/")


async def fetch_engine_trending(chain: str = "solana", limit: int = 20) -> dict[str, Any]:
    """Return the engine payload verbatim; raise :class:`TrendingUnavailable` on failure."""
    url = f"{engine_base()}/api/v1/trending"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params={"chain": chain, "limit": limit})
    except Exception as exc:
        raise TrendingUnavailable(f"transport:{type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise TrendingUnavailable(f"http:{resp.status_code}")
    body = resp.json()
    if not isinstance(body, dict):
        raise TrendingUnavailable("malformed:not_object")
    if body.get("engine") != ENGINE_ID:
        raise TrendingUnavailable(f"wrong_engine:{body.get('engine')}")
    return body
