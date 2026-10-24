"""Wallet intelligence normalization — smart money, stats, activity.

Input is the provider's already-normalized wallet detail (stats + activity).
Signed ``/v1/user/wallet_holdings`` is deliberately NOT called: it requires
GMGN_PRIVATE_KEY, which the fleet does not hold, so holdings stay null with a
reason instead of a fabricated balance.
"""

from __future__ import annotations

from typing import Any

from app.domains.gmgn_intel.coerce import canonical_chain, gap, iso_ts, num, text, whole

WALLET_SOURCES = ["GET /v1/user/wallet_stats", "GET /v1/user/wallet_activity"]
ACTIVITY_LIMIT = 25


def _activity(raw: dict[str, Any]) -> dict[str, Any]:
    """Map one activity row defensively and keep the raw facts alongside."""
    token = raw.get("token") if isinstance(raw.get("token"), dict) else {}
    return {
        "token_address": text(raw.get("token_address")) or text(token.get("address")) or text(raw.get("address")),
        "token_symbol": text(raw.get("token_symbol")) or text(token.get("symbol")) or text(raw.get("symbol")),
        "side": text(raw.get("side")) or text(raw.get("direction")) or text(raw.get("type")),
        "amount_usd": num(raw.get("amount_usd")) or num(raw.get("cost")) or num(raw.get("amount")),
        "timestamp": iso_ts(raw.get("timestamp")) or iso_ts(raw.get("block_timestamp")),
        "raw": raw,
    }


def build_wallet_profile(
    detail: dict[str, Any] | None,
    wallet: str,
    chain: str,
    period: str,
    observed_at: str,
) -> dict[str, Any]:
    """Provider wallet detail -> our wallet profile (nulls carry reasons)."""
    data = detail or {}
    available = data.get("source") == "gmgn"
    activities = [_activity(row) for row in (data.get("activities") or []) if isinstance(row, dict)]
    profile: dict[str, Any] = {
        "wallet": text(data.get("wallet")) or wallet,
        "chain": canonical_chain(chain),
        "period": period,
        "realized_profit_usd": num(data.get("realized_profit")),
        "realized_profit_pnl": num(data.get("realized_profit_pnl")),
        "winrate": num(data.get("winrate")),
        "tokens_traded": whole(data.get("token_num")),
        "avg_holding_period_seconds": num(data.get("avg_holding_period")),
        "pnl_over_5x_count": whole(data.get("pnl_gt_5x_num")),
        "trades": {"buy": whole(data.get("buy")), "sell": whole(data.get("sell"))},
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
        "activity": activities[:ACTIVITY_LIMIT],
        "holdings": None,
        "sources": list(WALLET_SOURCES),
        "observed_at": observed_at,
        "missing": [],
    }
    if not available:
        profile["missing"].append(gap("wallet", "gmgn wallet_stats returned no data"))
    if profile["winrate"] is None:
        profile["missing"].append(gap("winrate", "pnl_stat absent in wallet_stats"))
    if not activities:
        profile["missing"].append(gap("activity", "wallet_activity returned no rows"))
    profile["missing"].append(gap("holdings", "signed /v1/user/wallet_holdings requires GMGN_PRIVATE_KEY (not held)"))
    return profile
