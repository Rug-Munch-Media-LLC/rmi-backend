"""Normalize raw GMGN trenches rows into our own token shape.

Source: GMGN ``POST /v1/trenches`` (buckets ``new_creation`` /
``near_completion`` / ``completed``). We keep GMGN's *facts* and discard its
ordering — our score is attached separately by
:func:`app.domains.gmgn_intel.scoring.score_token`.
"""

from __future__ import annotations

from typing import Any

from app.domains.gmgn_intel.coerce import canonical_chain, gap, iso_ts, num, text, whole

TRENCHES_ENDPOINT = "POST /v1/trenches"

_SECURITY_FIELDS = (
    "rug_ratio",
    "top_10_holder_rate",
    "suspected_insider_hold_rate",
    "dev_team_hold_rate",
    "bot_degen_rate",
    "bundler_trader_amount_rate",
    "fresh_wallet_rate",
    "entrapment_ratio",
)


def _security(row: dict[str, Any]) -> dict[str, Any]:
    """Security/quality hints GMGN already returns on the trenches row."""
    sec: dict[str, Any] = {name: num(row.get(name)) for name in _SECURITY_FIELDS}
    sec["burn_status"] = text(row.get("burn_status"))
    sec["renounced_mint"] = row.get("renounced_mint")
    sec["is_wash_trading"] = row.get("is_wash_trading")
    sec["dev_token_burn_ratio"] = num(row.get("dev_token_burn_ratio"))
    return sec


def _gaps(token: dict[str, Any]) -> list[dict[str, str]]:
    """Fields we could not fill, each with a machine-readable reason."""
    out = [
        gap(field, reason)
        for field, reason in (
            ("liquidity_usd", "null/unparseable in source row"),
            ("volume_24h_usd", "null/unparseable in source row"),
            ("holders", "null/unparseable in source row"),
            ("progress", "launchpad exposes no bonding progress"),
            ("created_at", "no creation timestamp on row"),
        )
        if token.get(field) is None
    ]
    if not any(token["social"].values()):
        out.append(gap("social", "no twitter/website/telegram on row"))
    if token.get("holders") == 0:
        out.append(gap("holders", "reported 0 holders at observation time"))
    return out


def normalize_row(row: dict[str, Any], stage: str, observed_at: str) -> dict[str, Any]:
    """One raw trenches row -> our normalized token (stage, evidence, gaps)."""
    token: dict[str, Any] = {
        "address": text(row.get("address")),
        "chain": canonical_chain(row.get("chain")),
        "symbol": text(row.get("symbol")),
        "name": text(row.get("name")),
        "stage": stage,
        "trending": False,
        "trending_rank": None,
        "price": num(row.get("price")),
        "liquidity_usd": num(row.get("liquidity")),
        "market_cap_usd": num(row.get("market_cap")),
        "volume_24h_usd": num(row.get("volume_24h")),
        "holders": whole(row.get("holder_count")),
        "swaps_24h": whole(row.get("swaps_24h")),
        "buys_24h": whole(row.get("buys_24h")),
        "sells_24h": whole(row.get("sells_24h")),
        "progress": num(row.get("progress")),
        "launchpad": text(row.get("launchpad")) or text(row.get("launchpad_platform")),
        "launchpad_status": whole(row.get("launchpad_status")),
        "creator": text(row.get("creator")),
        "created_at": iso_ts(row.get("created_timestamp")),
        "graduated_at": iso_ts(row.get("complete_timestamp")),
        "social": {key: text(row.get(key)) for key in ("twitter", "website", "telegram")},
        "security": _security(row),
        "score": None,
        "score_grade": None,
        "score_breakdown": None,
        "evidence": [
            {
                "source": TRENCHES_ENDPOINT,
                "bucket": stage,
                "observed_at": observed_at,
            }
        ],
        "missing": [],
    }
    token["missing"] = _gaps(token)
    return token
