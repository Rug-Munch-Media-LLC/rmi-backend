"""Trending service — a single proxy to the canonical engine.

History: this module built its own candidate set from ClickHouse rows and
ranked it locally with entity/quote penalties. That was a
second scorer; it is gone. ``build_trending`` now returns the engine payload
unchanged so consumers get ``ranked`` / ``paid`` / ``disclosures`` / ``stale``
exactly as published. The raw row readers (``ch_client``, ``dex_rows``,
``parquet_source``, ``pumpfun``) stay — they are accessors, not rankers.
"""

from __future__ import annotations

from typing import Any

from app.trending.engine import fetch_engine_trending

__all__ = ["build_trending"]


async def build_trending(chain: str = "solana", hours: int = 24, limit: int = 20) -> dict[str, Any]:
    """Return the canonical ranked payload (``hours`` kept for caller compatibility)."""
    del hours  # window is owned by the engine's weight config
    return await fetch_engine_trending(chain, limit)
