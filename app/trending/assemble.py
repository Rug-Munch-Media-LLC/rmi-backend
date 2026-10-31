"""Turn indexed rows into trending items.

Item construction is deliberately separate from source fetching: callers hand
in the rows they already resolved, and every field we cannot source carries a
``null_reasons`` entry instead of a guessed value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.token_icons.fallback import fallback_icon_url
from app.trending.scoring import score_row


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


def _age_hours(row: dict[str, Any]) -> float | None:
    last_ts = row.get("last_ts")
    if not isinstance(last_ts, datetime):
        return None
    return (datetime.now(UTC) - last_ts.replace(tzinfo=UTC)).total_seconds() / 3600.0


def _base_item(row: dict[str, Any]) -> dict[str, Any]:
    nulls: dict[str, str] = {}
    price = _resolve_price(row, nulls)
    change = _resolve_change(row, nulls)
    trades = int(row.get("trades") or 0)
    buys = int(row.get("buys") or 0)
    last_ts = row.get("last_ts")

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
        "age_hours": _age_hours(row),
    }


def _apply_pumpfun(
    item: dict[str, Any], meta: dict[str, Any], sol_price: float | None
) -> None:
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


def build_items(
    rows: list[dict[str, Any]],
    entities: dict[str, Any],
    pumpfun: dict[str, Any],
    sol_price: float | None,
    hours: int,
) -> dict[str, dict[str, Any]]:
    """Build one item per row, keyed ``chain:token``, with its score."""
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = _base_item(row)
        entity = entities.get(row["token"])
        if entity and entity.get("entity_type"):
            item["null_reasons"].setdefault(
                "entity", f"labelled:{entity['entity_type']}"
            )
        meta = pumpfun.get(row["token"])
        if meta:
            _apply_pumpfun(item, meta, sol_price)
        item["score"] = score_row({**item, "entity": entity}, hours)
        items[f"{row['chain']}:{row['token']}"] = item
    return items


def merge_parquet(
    items: dict[str, dict[str, Any]], extra: list[dict[str, Any]]
) -> None:
    """Fill only still-null fields from the Parquet overlay."""
    for row in extra:
        target = items.get(f"{row.get('chain')}:{row.get('token')}")
        if target is None:
            continue
        for field in (
            "market_cap",
            "liquidity",
            "bonding_progress",
            "price_change_pct",
        ):
            value = row.get(field)
            if value is not None and target.get(field) is None:
                target[field] = float(value)
                target["null_reasons"].pop(field, None)
        target["source"] = f"{target['source']}+parquet"
