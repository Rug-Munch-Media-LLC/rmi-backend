"""Manipulation penalties for the transparent trending score.

One function per rule, each returning ``(multiplier, entry-or-None)`` and
keeping its own threshold from :mod:`app.services.trending.config`, so a
demotion is always named and never applied silently.
"""

from __future__ import annotations

from typing import Any

from app.services.trending import config as cfg


def _wash_trading(
    row: dict[str, Any], _boost: dict[str, Any] | None
) -> tuple[float, dict[str, Any] | None]:
    swaps, senders = int(row.get("swaps") or 0), int(row.get("senders") or 0)
    if not (swaps and senders):
        return 1.0, None
    per_wallet = swaps / senders
    if per_wallet <= cfg.WASH_SWAPS_PER_WALLET:
        return 1.0, None
    f = min(cfg.WASH_MAX_PENALTY, (per_wallet - cfg.WASH_SWAPS_PER_WALLET) / 40.0)
    entry = {
        "kind": "wash_trading",
        "swaps_per_wallet": round(per_wallet, 2),
        "multiplier": round(1.0 - f, 3),
    }
    return 1.0 - f, entry


def _single_wallet_volume(
    row: dict[str, Any], _boost: dict[str, Any] | None
) -> tuple[float, dict[str, Any] | None]:
    swaps, senders = int(row.get("swaps") or 0), int(row.get("senders") or 0)
    if not (swaps and senders):
        return 1.0, None
    share = int(row.get("top_sender_swaps") or 0) / swaps
    if share <= cfg.CONCENTRATION_SHARE:
        return 1.0, None
    f = min(cfg.CONCENTRATION_MAX_PENALTY, (share - cfg.CONCENTRATION_SHARE) * 2.0)
    entry = {
        "kind": "single_wallet_volume",
        "top_wallet_share": round(share, 3),
        "multiplier": round(1.0 - f, 3),
    }
    return 1.0 - f, entry


def _fresh_wallet_bundles(
    row: dict[str, Any], _boost: dict[str, Any] | None
) -> tuple[float, dict[str, Any] | None]:
    bundles = int(row.get("bundled_blocks") or 0)
    if bundles < cfg.BUNDLE_BLOCKS:
        return 1.0, None
    f = min(cfg.BUNDLE_MAX_PENALTY, bundles / 20.0)
    entry = {
        "kind": "fresh_wallet_bundles",
        "bundled_blocks": bundles,
        "multiplier": round(1.0 - f, 3),
    }
    return 1.0 - f, entry


def _suspicious_boost(
    row: dict[str, Any], boost: dict[str, Any] | None
) -> tuple[float, dict[str, Any] | None]:
    if not boost or float(row.get("volume_usd") or 0) >= cfg.BOOST_LOW_VOLUME_USD:
        return 1.0, None
    f = cfg.BOOST_MAX_PENALTY
    entry = {
        "kind": "suspicious_boost",
        "boost_amount": boost.get("amount"),
        "volume_usd": row.get("volume_usd"),
        "multiplier": round(1.0 - f, 3),
    }
    return 1.0 - f, entry


RULES = (
    _wash_trading,
    _single_wallet_volume,
    _fresh_wallet_bundles,
    _suspicious_boost,
)


def penalties(
    row: dict[str, Any], boost: dict[str, Any] | None
) -> tuple[float, list[dict[str, Any]]]:
    """Return ``(multiplier, applied)``; the floor is 0.2 so no token is zeroed."""
    mult = 1.0
    applied: list[dict[str, Any]] = []
    for rule in RULES:
        factor, entry = rule(row, boost)
        if entry is None:
            continue
        mult *= factor
        applied.append(entry)
    return round(max(0.2, mult), 4), applied
