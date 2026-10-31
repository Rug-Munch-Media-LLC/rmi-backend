"""Assemble the trending response from the indexed sources.

Each source is isolated: a failure is recorded in ``degraded`` and the feed
keeps serving from whatever is left. Nullable fields carry a reason instead of
a guessed value.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.token_icons.cache import peek_icon_url
from app.trending.assemble import build_items, merge_parquet
from app.trending.dex_rows import fetch_dex_rows
from app.trending.entity_meta import fetch_entities
from app.trending.parquet_source import fetch_parquet_rows
from app.trending.pumpfun import fetch_pumpfun
from app.trending.quotes import is_wrapped_native

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("trending.service")

CANDIDATE_MULTIPLIER = 12


async def _optional(name: str, fn: Callable[..., Any], *args: Any, default: Any, degraded: list[str]) -> Any:
    """Run one source in a thread; record and swallow any failure."""
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception as exc:  # fail-open by design
        logger.warning("%s source failed: %s", name, exc)
        degraded.append(name)
        return default


def _sol_price(rows: list[dict[str, Any]]) -> float | None:
    """Latest indexed wSOL price, used to convert SOL market caps."""
    for row in rows:
        token = str(row.get("token"))
        if row.get("chain") == "solana" and is_wrapped_native("solana", token) and row.get("last_px"):
            return float(row["last_px"])
    return None


async def _load_sources(
    chain: str, hours: int, limit: int, degraded: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Fetch candidates, then the metadata sources that depend on them."""
    rows = await _optional(
        "dex_events", fetch_dex_rows, chain, hours, limit * CANDIDATE_MULTIPLIER, default=[], degraded=degraded
    )
    sol_mints = [r["token"] for r in rows if r.get("chain") == "solana"]
    pumpfun = await _optional("pumpfun", fetch_pumpfun, sol_mints, default={}, degraded=degraded)
    entities = await _optional(
        "entity_labels", fetch_entities, [r["token"] for r in rows], default={}, degraded=degraded
    )
    return rows, pumpfun, entities


def _finalize(items: dict[str, dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Rank, icon-peek (L1 only) and drop the internal age field."""
    ordered = sorted(items.values(), key=lambda i: i["score"], reverse=True)[:limit]
    for item in ordered:
        # L1-only peek: a cached icon is free, a miss keeps the branded fallback.
        item["icon_url"] = peek_icon_url(item["chain"], item["token"]) or item["icon_url"]
        item.pop("age_hours", None)
    return ordered


async def build_trending(chain: str, hours: int, limit: int) -> dict[str, Any]:
    """Build the trending payload. Never raises for source failures."""
    degraded: list[str] = []
    rows, pumpfun, entities = await _load_sources(chain, hours, limit, degraded)
    items = build_items(rows, entities, pumpfun, _sol_price(rows), hours)
    extra = await _optional("parquet", fetch_parquet_rows, default=[], degraded=degraded)
    if extra:
        merge_parquet(items, extra)
    ordered = _finalize(items, limit)
    source = "clickhouse+parquet" if extra else "clickhouse"
    return {
        "window_hours": hours,
        "chain": chain or None,
        "items": ordered,
        "generated_at": datetime.now(UTC),
        "degraded": degraded,
        "source": source if ordered else "empty",
    }
