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


def score_row(row: dict[str, Any], hours: int) -> float:
    """Compute the 0-100 score for one aggregated token row."""
    volume = float(row.get("volume_usd") or 0.0)
    trades = int(row.get("trades") or 0)
    traders = int(row.get("traders") or 0)
    buys = int(row.get("buys") or 0)
    pct = row.get("price_change_pct")

    score = 40.0 * _clip(math.log10(1.0 + volume) / 6.0, 0.0, 1.0)
    score += 20.0 * _clip(trades / 150.0, 0.0, 1.0)
    score += 15.0 * _clip(traders / 80.0, 0.0, 1.0)
    score += 15.0 * (0.5 if pct is None else _clip((float(pct) + 50.0) / 200.0, 0.0, 1.0))
    score += 10.0 * (0.5 if trades == 0 else _clip(buys / trades, 0.0, 1.0))

    score *= quote_penalty(str(row.get("chain") or ""), str(row.get("token") or ""))
    score *= entity_penalty(row.get("entity"))
    if row.get("price") is None:
        score *= 0.85
    age_hours = row.get("age_hours")
    if age_hours is not None and float(age_hours) > max(hours * 0.75, 0.25):
        score *= 0.7
    return round(_clip(score, 0.0, 100.0), 2)
