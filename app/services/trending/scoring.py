"""Transparent scoring: every component's raw value, weight and contribution
are returned, so a caller can re-derive the score without trusting us.

The factors live in :mod:`~app.services.trending.factors` and the manipulation
rules in :mod:`~app.services.trending.penalties`; this module only composes
them into a ranked payload.
"""

from __future__ import annotations

from typing import Any

from app.services.trending.factors import factors
from app.services.trending.penalties import penalties


def _reasons(components: dict[str, dict[str, Any]], applied: list[dict[str, Any]], row: dict[str, Any]) -> list[str]:
    unit = "transfers" if row.get("transfers_24h") is not None else "swaps"
    out = [f"tx_count={components['tx_count']['value']} {unit}/24h"]
    if components["volume_liquidity"]["status"] != "null":
        out.append(f"volume_liquidity=${float(components['volume_liquidity']['value']):,.0f}/24h")
    if components["holder_growth"]["status"] == "ok":
        g = components["holder_growth"]["value"]
        out.append(f"holder_growth={g['growth']:+.2%} ({g['prev']}→{g['now']} wallets)")
    if components["price_momentum"]["status"] == "ok":
        out.append(f"price_momentum={float(components['price_momentum']['value']):+.2%}")
    if components["age"]["status"] == "ok":
        out.append(f"age={float(components['age']['value']):.1f}h")
    if components["social_sentiment"]["status"] == "ok":
        s = components["social_sentiment"]["value"]
        out.append(f"social={s['mentions']} mentions polarity={s['polarity']:+.2f}")
    out.append(f"source_agreement={components['source_agreement']['score']:.2f}")
    out.extend(f"penalty {p['kind']} x{p['multiplier']}" for p in applied)
    return out


def _weighted_raw(components: dict[str, dict[str, Any]]) -> float:
    """Weighted mean over the components that are actually present."""
    present = {k: v for k, v in components.items() if v["status"] == "ok"}
    total_w = sum(v["weight"] for v in present.values())
    if not total_w:
        return 0.0
    return sum(v["weight"] * v["score"] for v in present.values()) / total_w


def _item(row: dict[str, Any], boost: dict[str, Any] | None, boosted: bool) -> dict[str, Any]:
    components = factors(row)
    raw = _weighted_raw(components)
    mult, applied = penalties(row, boost)
    item: dict[str, Any] = {
        "chain": row["chain"],
        "token_address": row["token_address"],
        "symbol": row.get("symbol") or "",
        "name": "",
        "address": row["token_address"],
        "price": None,
        "change_24h": row.get("price_change"),
        "volume_24h": row.get("volume_usd"),
        "market_cap": None,
        "score": round(100.0 * raw * mult, 2),
        "score_raw": round(100.0 * raw, 2),
        "penalty_multiplier": mult,
        "score_components": components,
        "penalties": applied,
        "reasons": _reasons(components, applied, row),
        "icon_url": (boost or {}).get("icon"),
        "source": "own_clickhouse:multifactor",
    }
    if boosted:
        item["rank"] = None
        item["boosted"] = True
        item["excluded_from_rank"] = True
        item["boost"] = {"amount": (boost or {}).get("amount"), "total_amount": (boost or {}).get("total_amount")}
    else:
        item["boosted"] = False
    return item


def score_candidates(
    rows: list[dict[str, Any]],
    boosted_keys: set[tuple[str, str]],
    boosts: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(ranked, boosted)``. Boosted tokens never enter ``ranked``."""
    boosts = boosts or {}
    ranked: list[dict[str, Any]] = []
    boosted_out: list[dict[str, Any]] = []
    for row in rows:
        key = (row["chain"], row["token_address"])
        item = _item(row, boosts.get(key), key in boosted_keys)
        if item["boosted"]:
            boosted_out.append(item)
        else:
            ranked.append(item)
    ranked.sort(key=lambda x: x["score"], reverse=True)
    for position, item in enumerate(ranked, start=1):
        item["rank"] = position
    boosted_out.sort(key=lambda x: x["score"], reverse=True)
    return ranked, boosted_out
