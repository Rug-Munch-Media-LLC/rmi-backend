"""Transparent scoring: every component's raw value, weight and contribution
are returned, so a caller can re-derive the score without trusting us."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from app.services.trending import config as cfg

SOURCE_COUNT = 5  # transfers, ohlcv, dex_events, launches, social


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _log_scale(value: float, saturation: float) -> float:
    if value <= 0:
        return 0.0
    return _clamp(math.log10(value + 1.0) / math.log10(saturation + 1.0))


def _age_hours(row: dict[str, Any]) -> float | None:
    raw = row.get("launched_at")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - ts).total_seconds() / 3600.0)


def _component(value: float, weight: float, status: str, detail: Any = None) -> dict[str, Any]:
    return {
        "value": detail if detail is not None else round(value, 4),
        "weight": weight,
        "score": round(_clamp(value), 4),
        "status": status,
    }


def _factors(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-component normalised score (0..1) or a ``null`` component."""
    out: dict[str, dict[str, Any]] = {}
    w = cfg.WEIGHTS

    # DEX tx count: realised transfers when the transfer indexer covers the
    # chain, otherwise the measured swap count from dex_events.
    transfers = row.get("transfers_24h")
    tx_value = float(transfers if transfers is not None else (row.get("swaps") or 0))
    out["tx_count"] = _component(_log_scale(tx_value, cfg.TX_SATURATION), w["tx_count"], "ok", int(tx_value))

    volume = row.get("volume_usd")
    if volume is None:
        out["volume_liquidity"] = _component(0.0, w["volume_liquidity"], "null", None)
    else:
        out["volume_liquidity"] = _component(
            _log_scale(float(volume), cfg.VOLUME_SATURATION_USD), w["volume_liquidity"], "ok", round(float(volume), 2)
        )

    now_h, prev_h = int(row.get("holders_now") or 0), int(row.get("holders_prev") or 0)
    if prev_h <= 0:
        # No baseline window (indexer gap) — report it, never impute growth.
        out["holder_growth"] = _component(0.0, w["holder_growth"], "no_baseline", {"now": now_h, "prev": prev_h})
    else:
        growth = (now_h - prev_h) / prev_h
        out["holder_growth"] = _component(
            _clamp(0.5 + math.tanh(growth) / 2.0),
            w["holder_growth"],
            "ok",
            {"now": now_h, "prev": prev_h, "growth": round(growth, 4)},
        )

    change = row.get("price_change")
    if change is None:
        out["price_momentum"] = _component(0.0, w["price_momentum"], "null", None)
    else:
        out["price_momentum"] = _component(
            _clamp(0.5 + math.tanh(float(change) * 2.0) / 2.0), w["price_momentum"], "ok", round(float(change), 4)
        )

    age = _age_hours(row)
    if age is None:
        out["age"] = _component(0.0, w["age"], "null", None)
    else:
        out["age"] = _component(_clamp(0.5 ** (age / cfg.AGE_HALF_LIFE_HOURS)), w["age"], "ok", round(age, 2))

    social = row.get("social") or {}
    if not social.get("mentions"):
        out["social_sentiment"] = _component(0.0, w["social_sentiment"], "null", None)
    else:
        polarity = float(social.get("polarity") or 0.0)
        volume_f = min(1.0, float(social["mentions"]) / 5.0)
        out["social_sentiment"] = _component(
            _clamp(((polarity + 1.0) / 2.0) * volume_f), w["social_sentiment"], "ok", social
        )

    sources = row.get("sources") or []
    out["source_agreement"] = _component(
        len(set(sources)) / SOURCE_COUNT, w["source_agreement"], "ok", sorted(set(sources))
    )
    return out


def _penalties(row: dict[str, Any], boost: dict[str, Any] | None) -> tuple[float, list[dict[str, Any]]]:
    mult, applied = 1.0, []
    swaps, senders = int(row.get("swaps") or 0), int(row.get("senders") or 0)
    if swaps and senders:
        per_wallet = swaps / senders
        if per_wallet > cfg.WASH_SWAPS_PER_WALLET:
            f = min(cfg.WASH_MAX_PENALTY, (per_wallet - cfg.WASH_SWAPS_PER_WALLET) / 40.0)
            mult *= 1.0 - f
            applied.append(
                {"kind": "wash_trading", "swaps_per_wallet": round(per_wallet, 2), "multiplier": round(1.0 - f, 3)}
            )
        share = int(row.get("top_sender_swaps") or 0) / swaps
        if share > cfg.CONCENTRATION_SHARE:
            f = min(cfg.CONCENTRATION_MAX_PENALTY, (share - cfg.CONCENTRATION_SHARE) * 2.0)
            mult *= 1.0 - f
            applied.append(
                {"kind": "single_wallet_volume", "top_wallet_share": round(share, 3), "multiplier": round(1.0 - f, 3)}
            )
    bundles = int(row.get("bundled_blocks") or 0)
    if bundles >= cfg.BUNDLE_BLOCKS:
        f = min(cfg.BUNDLE_MAX_PENALTY, bundles / 20.0)
        mult *= 1.0 - f
        applied.append({"kind": "fresh_wallet_bundles", "bundled_blocks": bundles, "multiplier": round(1.0 - f, 3)})
    if boost and float(row.get("volume_usd") or 0) < cfg.BOOST_LOW_VOLUME_USD:
        f = cfg.BOOST_MAX_PENALTY
        mult *= 1.0 - f
        applied.append(
            {
                "kind": "suspicious_boost",
                "boost_amount": boost.get("amount"),
                "volume_usd": row.get("volume_usd"),
                "multiplier": round(1.0 - f, 3),
            }
        )
    return round(max(0.2, mult), 4), applied


def _reasons(components: dict[str, dict[str, Any]], penalties: list[dict[str, Any]], row: dict[str, Any]) -> list[str]:
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
    out.extend(f"penalty {p['kind']} x{p['multiplier']}" for p in penalties)
    return out


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
        boost = boosts.get(key)
        components = _factors(row)
        present = {k: v for k, v in components.items() if v["status"] == "ok"}
        total_w = sum(v["weight"] for v in present.values())
        raw = sum(v["weight"] * v["score"] for v in present.values()) / total_w if total_w else 0.0
        mult, penalties = _penalties(row, boost)
        item = {
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
            "penalties": penalties,
            "reasons": _reasons(components, penalties, row),
            "icon_url": (boost or {}).get("icon"),
            "source": "own_clickhouse:multifactor",
        }
        if key in boosted_keys:
            item["rank"] = None
            item["boosted"] = True
            item["excluded_from_rank"] = True
            item["boost"] = {"amount": (boost or {}).get("amount"), "total_amount": (boost or {}).get("total_amount")}
            boosted_out.append(item)
        else:
            item["boosted"] = False
            ranked.append(item)
    ranked.sort(key=lambda x: x["score"], reverse=True)
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i
        item = {**item, "rank": i}
        item["rank"] = i
    boosted_out.sort(key=lambda x: x["score"], reverse=True)
    return ranked, boosted_out
