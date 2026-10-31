"""Per-component normalised factors for the transparent trending score.

Each component is built in its own function and returned as
``{value, weight, score, status}``, so a caller can re-derive the score
without trusting us. A missing input is reported as ``null`` — never imputed.
"""

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


def _component(
    value: float, weight: float, status: str, detail: Any = None
) -> dict[str, Any]:
    return {
        "value": detail if detail is not None else round(value, 4),
        "weight": weight,
        "score": round(_clamp(value), 4),
        "status": status,
    }


def _tx_count(row: dict[str, Any]) -> dict[str, Any]:
    """Realised transfers when the indexer covers the chain, else swap count."""
    transfers = row.get("transfers_24h")
    value = float(transfers if transfers is not None else (row.get("swaps") or 0))
    score = _log_scale(value, cfg.TX_SATURATION)
    return _component(score, cfg.WEIGHTS["tx_count"], "ok", int(value))


def _volume(row: dict[str, Any]) -> dict[str, Any]:
    volume = row.get("volume_usd")
    weight = cfg.WEIGHTS["volume_liquidity"]
    if volume is None:
        return _component(0.0, weight, "null", None)
    score = _log_scale(float(volume), cfg.VOLUME_SATURATION_USD)
    return _component(score, weight, "ok", round(float(volume), 2))


def _holder_growth(row: dict[str, Any]) -> dict[str, Any]:
    now_h, prev_h = int(row.get("holders_now") or 0), int(row.get("holders_prev") or 0)
    weight = cfg.WEIGHTS["holder_growth"]
    detail: dict[str, Any] = {"now": now_h, "prev": prev_h}
    if prev_h <= 0:
        # No baseline window (indexer gap) — report it, never impute growth.
        return _component(0.0, weight, "no_baseline", detail)
    growth = (now_h - prev_h) / prev_h
    detail["growth"] = round(growth, 4)
    return _component(_clamp(0.5 + math.tanh(growth) / 2.0), weight, "ok", detail)


def _price_momentum(row: dict[str, Any]) -> dict[str, Any]:
    change = row.get("price_change")
    weight = cfg.WEIGHTS["price_momentum"]
    if change is None:
        return _component(0.0, weight, "null", None)
    score = _clamp(0.5 + math.tanh(float(change) * 2.0) / 2.0)
    return _component(score, weight, "ok", round(float(change), 4))


def _age(row: dict[str, Any]) -> dict[str, Any]:
    age = _age_hours(row)
    weight = cfg.WEIGHTS["age"]
    if age is None:
        return _component(0.0, weight, "null", None)
    score = _clamp(0.5 ** (age / cfg.AGE_HALF_LIFE_HOURS))
    return _component(score, weight, "ok", round(age, 2))


def _social_sentiment(row: dict[str, Any]) -> dict[str, Any]:
    social = row.get("social") or {}
    weight = cfg.WEIGHTS["social_sentiment"]
    if not social.get("mentions"):
        return _component(0.0, weight, "null", None)
    polarity = float(social.get("polarity") or 0.0)
    volume_f = min(1.0, float(social["mentions"]) / 5.0)
    return _component(_clamp(((polarity + 1.0) / 2.0) * volume_f), weight, "ok", social)


def _source_agreement(row: dict[str, Any]) -> dict[str, Any]:
    sources = sorted(set(row.get("sources") or []))
    return _component(
        len(sources) / SOURCE_COUNT, cfg.WEIGHTS["source_agreement"], "ok", sources
    )


def factors(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-component normalised score (0..1) or a ``null`` component."""
    return {
        "tx_count": _tx_count(row),
        "volume_liquidity": _volume(row),
        "holder_growth": _holder_growth(row),
        "price_momentum": _price_momentum(row),
        "age": _age(row),
        "social_sentiment": _social_sentiment(row),
        "source_agreement": _source_agreement(row),
    }
