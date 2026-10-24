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
from app.token_icons.fallback import fallback_icon_url
from app.trending.dex_rows import fetch_dex_rows
from app.trending.entity_meta import fetch_entities
from app.trending.parquet_source import fetch_parquet_rows
from app.trending.pumpfun import fetch_pumpfun
from app.trending.quotes import is_wrapped_native
from app.trending.scoring import score_row

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


def _resolve_price(row: dict[str, Any], nulls: dict[str, str]) -> float | None:
    price = row.get("last_px")
    if price is None:
        nulls["price"] = "price_series_unavailable"
        return None
    return round(float(price), 12)


def _resolve_change(row: dict[str, Any], nulls: dict[str, str]) -> float | None:
    first, last = row.get("first_px"), row.get("last_px")
    if first is None or last is None:
        nulls["price_change_pct"] = "single_trade_in_window"
        return None
    if float(first) <= 0:
        nulls["price_change_pct"] = "non_positive_reference_price"
        return None
    return round((float(last) - float(first)) / float(first) * 100.0, 4)


def _base_item(row: dict[str, Any]) -> dict[str, Any]:
    nulls: dict[str, str] = {}
    price = _resolve_price(row, nulls)
    change = _resolve_change(row, nulls)
    trades = int(row.get("trades") or 0)
    buys = int(row.get("buys") or 0)
    last_ts = row.get("last_ts")
    age_hours = None
    if isinstance(last_ts, datetime):
        age_hours = (datetime.now(UTC) - last_ts.replace(tzinfo=UTC)).total_seconds() / 3600.0

    symbol = row.get("symbol") or None
    if not symbol:
        nulls["symbol"] = "not_in_token_meta_ref"
    nulls.setdefault("market_cap", "market_cap_not_indexed")
    nulls.setdefault("liquidity", "reserves_not_indexed")
    nulls.setdefault("bonding_progress", "not_a_launchpad_token")

    return {
        "chain": row["chain"],
        "token": row["token"],
        "symbol": symbol,
        "name": row.get("name") or None,
        "price": price,
        "market_cap": None,
        "liquidity": None,
        "price_change_pct": change,
        "volume_usd": round(float(row.get("volume_usd") or 0.0), 2),
        "trades": trades,
        "buys": buys,
        "sells": max(trades - buys, 0),
        "bonding_progress": None,
        "source": "dex_events",
        "icon_url": fallback_icon_url(row["chain"], row["token"]),
        "last_event_at": last_ts if isinstance(last_ts, datetime) else None,
        "null_reasons": nulls,
        "age_hours": age_hours,
    }


def _apply_pumpfun(item: dict[str, Any], meta: dict[str, Any], sol_price: float | None) -> None:
    """Overlay launchpad metadata for a Solana mint."""
    item["source"] = "dex_events+pumpfun"
    nulls = item["null_reasons"]
    if meta.get("symbol"):
        item["symbol"] = meta["symbol"]
        nulls.pop("symbol", None)
    if meta.get("name"):
        item["name"] = meta["name"]
    if meta.get("market_cap_sol") is not None and sol_price:
        item["market_cap"] = round(float(meta["market_cap_sol"]) * sol_price, 2)
        nulls.pop("market_cap", None)
    if meta.get("bonding_progress") is not None:
        item["bonding_progress"] = round(float(meta["bonding_progress"]), 4)


def _sol_price(rows: list[dict[str, Any]]) -> float | None:
    """Latest indexed wSOL price, used to convert SOL market caps."""
    for row in rows:
        token = str(row.get("token"))
        if row.get("chain") == "solana" and is_wrapped_native("solana", token) and row.get("last_px"):
            return float(row["last_px"])
    return None


def _merge_parquet(items: dict[str, dict[str, Any]], extra: list[dict[str, Any]]) -> None:
    """Fill only still-null fields from the Parquet overlay."""
    for row in extra:
        target = items.get(f"{row.get('chain')}:{row.get('token')}")
        if target is None:
            continue
        for field in ("market_cap", "liquidity", "bonding_progress", "price_change_pct"):
            value = row.get(field)
            if value is not None and target.get(field) is None:
                target[field] = float(value)
                target["null_reasons"].pop(field, None)
        target["source"] = f"{target['source']}+parquet"


async def build_trending(chain: str, hours: int, limit: int) -> dict[str, Any]:
    """Build the trending payload. Never raises for source failures."""
    degraded: list[str] = []
    rows = await _optional(
        "dex_events",
        fetch_dex_rows,
        chain,
        hours,
        limit * CANDIDATE_MULTIPLIER,
        default=[],
        degraded=degraded,
    )
    sol_mints = [r["token"] for r in rows if r.get("chain") == "solana"]
    pumpfun = await _optional("pumpfun", fetch_pumpfun, sol_mints, default={}, degraded=degraded)
    entities = await _optional(
        "entity_labels",
        fetch_entities,
        [r["token"] for r in rows],
        default={},
        degraded=degraded,
    )

    sol_price = _sol_price(rows)
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = _base_item(row)
        entity = entities.get(row["token"])
        if entity and entity.get("entity_type"):
            item["null_reasons"].setdefault("entity", f"labelled:{entity['entity_type']}")
        meta = pumpfun.get(row["token"])
        if meta:
            _apply_pumpfun(item, meta, sol_price)
        item["score"] = score_row({**item, "entity": entity}, hours)
        items[f"{row['chain']}:{row['token']}"] = item

    extra = await _optional("parquet", fetch_parquet_rows, default=[], degraded=degraded)
    if extra:
        _merge_parquet(items, extra)

    ordered = sorted(items.values(), key=lambda i: i["score"], reverse=True)[:limit]
    for item in ordered:
        # L1-only peek: a cached icon is free, a miss keeps the branded fallback.
        item["icon_url"] = peek_icon_url(item["chain"], item["token"]) or item["icon_url"]
    for item in ordered:
        item.pop("age_hours", None)
    source = "clickhouse+parquet" if extra else "clickhouse"
    return {
        "window_hours": hours,
        "chain": chain or None,
        "items": ordered,
        "generated_at": datetime.now(UTC),
        "degraded": degraded,
        "source": source if ordered else "empty",
    }
