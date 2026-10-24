"""Normalizers for GMGN token-detail and market-rank payloads.

Kept apart from the trenches normalizer: those rows carry bonding progress,
these carry price/volume windows and socials. Both land in the same token
shape so downstream consumers never branch on the source.
"""

from __future__ import annotations

from typing import Any

from app.domains.gmgn_intel.coerce import canonical_chain, gap, iso_ts, num, text, whole

INFO_ENDPOINT = "GET /v1/token/info"
RANK_ENDPOINT = "GET /v1/market/rank"


def _social_list(links: Any) -> dict[str, str | None]:
    """Socials from either the ``link`` object or flat twitter/website keys."""
    source = links if isinstance(links, dict) else {}
    out: dict[str, str | None] = {}
    for key in ("twitter", "website", "telegram"):
        out[key] = text(source.get(key)) or text(source.get(f"{key}_url"))
    return out


def normalize_token_info(info: dict[str, Any] | None, address: str, chain: str, observed_at: str) -> dict[str, Any]:
    """GET /v1/token/info -> our token shape (price window + lifecycle facts)."""
    data = info or {}
    price_block = data.get("price") if isinstance(data.get("price"), dict) else {}
    price = num(price_block.get("price")) or num(data.get("price"))
    circulating = num(data.get("circulating_supply"))
    status = whole(data.get("launchpad_status")) or 0
    graduated_at = iso_ts(data.get("migrated_timestamp")) or iso_ts(data.get("complete_timestamp"))
    token: dict[str, Any] = {
        "address": text(data.get("address")) or address,
        "chain": canonical_chain(chain),
        "symbol": text(data.get("symbol")),
        "name": text(data.get("name")),
        "stage": "graduated" if (status >= 1 or graduated_at) else None,
        "trending": False,
        "trending_rank": None,
        "price": price,
        "liquidity_usd": num(data.get("liquidity")),
        "market_cap_usd": round(price * circulating, 2) if price and circulating else None,
        "volume_24h_usd": num(price_block.get("volume_24h")),
        "holders": whole(data.get("holder_count")),
        "swaps_24h": whole(price_block.get("swaps_24h")),
        "buys_24h": whole(price_block.get("buys_24h")),
        "sells_24h": whole(price_block.get("sells_24h")),
        "price_change_1h": num(price_block.get("price_1h")),
        "progress": num(data.get("launchpad_progress")),
        "launchpad": text(data.get("launchpad")) or text(data.get("launchpad_platform")),
        "launchpad_status": status,
        "created_at": iso_ts(data.get("creation_timestamp")),
        "graduated_at": graduated_at,
        "social": _social_list(data.get("link")),
        "security": {},
        "score": None,
        "score_grade": None,
        "score_breakdown": None,
        "evidence": [{"source": INFO_ENDPOINT, "observed_at": observed_at}],
        "missing": [],
    }
    if token["stage"] is None:
        token["missing"].append(gap("stage", "token info carries no graduation fact"))
    if not any(token["social"].values()):
        token["missing"].append(gap("social", "no social links in token info"))
    if token["market_cap_usd"] is None:
        token["missing"].append(gap("market_cap_usd", "price or circulating supply absent"))
    return token


def normalize_rank_row(row: dict[str, Any], observed_at: str) -> dict[str, Any]:
    """GET /v1/market/rank row -> our token shape (GMGN order recorded, not used)."""
    status = whole(row.get("launchpad_status")) or 0
    token: dict[str, Any] = {
        "address": text(row.get("address")),
        "chain": canonical_chain(row.get("chain")),
        "symbol": text(row.get("symbol")),
        "name": text(row.get("name")),
        "stage": "graduated" if status >= 1 else None,
        "trending": True,
        "trending_rank": whole(row.get("rank")),
        "price": num(row.get("price")),
        "liquidity_usd": num(row.get("liquidity")),
        "market_cap_usd": num(row.get("market_cap")),
        "volume_24h_usd": num(row.get("volume")),
        "holders": whole(row.get("holder_count")),
        "swaps_24h": whole(row.get("swaps")),
        "buys_24h": whole(row.get("buys")),
        "sells_24h": whole(row.get("sells")),
        "price_change_1h": num(row.get("price_change_percent1h")),
        "progress": None,
        "launchpad": text(row.get("launchpad")) or text(row.get("launchpad_platform")),
        "launchpad_status": status,
        "created_at": iso_ts(row.get("creation_timestamp")),
        "graduated_at": None,
        "social": _social_list(row),
        "security": {
            "is_honeypot": bool(row.get("is_honeypot") or False),
            "can_not_sell": bool(row.get("can_not_sell") or False),
            "rug_ratio": num(row.get("rug_ratio")),
            "top_10_holder_rate": num(row.get("top_10_holder_rate")),
            "sniper_count": whole(row.get("sniper_count")),
            "bot_degen_rate": num(row.get("bot_degen_rate")),
            "is_wash_trading": row.get("is_wash_trading"),
        },
        "smart_money_count": whole(row.get("smart_degen_count")),
        "renowned_count": whole(row.get("renowned_count")),
        "score": None,
        "score_grade": None,
        "score_breakdown": None,
        "evidence": [{"source": RANK_ENDPOINT, "observed_at": observed_at}],
        "missing": [],
    }
    if token["stage"] is None:
        token["missing"].append(gap("stage", "rank feed carries no bonding progress"))
    if not any(token["social"].values()):
        token["missing"].append(gap("social", "no social links on rank row"))
    return token
