"""Deterministic 0-100 trending score.

Components (sum = 100 before penalties):
  volume   40  log10(1+usd_volume) / 6, capped at 1
  activity 20  trades / 150, capped at 1
  breadth  15  unique traders / 80, capped at 1
  momentum 15  price change in [-50%, +150%] mapped to 0..1 (0.5 when unknown)
  pressure 10  buy share of trades (0.5 when unknown)

Penalties: quote/stable asset, wrapped native, known entity, no price series,
and stale activity (last event older than the last quarter of the window).
"""

from __future__ import annotations

import math
from typing import Any

from app.trending.entity_meta import entity_penalty
from app.trending.quotes import quote_penalty


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _activity(row: dict[str, Any]) -> float:
    """Volume, trade count and trader breadth, each already in 0..1."""
    volume = float(row.get("volume_usd") or 0.0)
    trades = int(row.get("trades") or 0)
    traders = int(row.get("traders") or 0)
    return (
        40.0 * _clip(math.log10(1.0 + volume) / 6.0, 0.0, 1.0)
        + 20.0 * _clip(trades / 150.0, 0.0, 1.0)
        + 15.0 * _clip(traders / 80.0, 0.0, 1.0)
    )


def _momentum(row: dict[str, Any]) -> float:
    pct = row.get("price_change_pct")
    if pct is None:
        return 0.5
    return _clip((float(pct) + 50.0) / 200.0, 0.0, 1.0)


def _pressure(row: dict[str, Any]) -> float:
    trades = int(row.get("trades") or 0)
    if trades == 0:
        return 0.5
    return _clip(int(row.get("buys") or 0) / trades, 0.0, 1.0)


def _deductions(row: dict[str, Any], hours: int) -> float:
    """Quote/entity veto, missing price series and stale activity."""
    mult = quote_penalty(str(row.get("chain") or ""), str(row.get("token") or ""))
    mult *= entity_penalty(row.get("entity"))
    if row.get("price") is None:
        mult *= 0.85
    age_hours = row.get("age_hours")
    if age_hours is not None and float(age_hours) > max(hours * 0.75, 0.25):
        mult *= 0.7
    return mult


def score_row(row: dict[str, Any], hours: int) -> float:
    """Compute the 0-100 score for one aggregated token row."""
    score = _activity(row) + 15.0 * _momentum(row) + 10.0 * _pressure(row)
    return round(_clip(score * _deductions(row, hours), 0.0, 100.0), 2)
