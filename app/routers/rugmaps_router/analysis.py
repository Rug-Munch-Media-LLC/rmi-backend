"""Fresh-wallet & cluster-depth analysis routes (BubbleMaps-superior intel):
/fresh-wallet-analysis/{chain}/{address}, /cluster-depth/{chain}/{address}.

Both endpoints are GET, deterministic, and never fabricate data: when a
signal cannot be derived from our holder graph / wallet-label stack, the
field reports ``null`` / ``"unknown"`` and ``coverage`` states how much of
the supply the derivation actually saw.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Query

from app.core.logging import get_logger
from app.routers.rugmaps_router._helpers import sub_router

logger = get_logger(__name__)

router = sub_router()

# Wallet-age extraction field candidates, most-specific first.
_AGE_FIELDS = ("wallet_age_days", "age_days", "wallet_age", "account_age_days")
_FIRST_TX_FIELDS = ("first_tx_ts", "first_tx_timestamp", "first_seen", "created_at", "first_activity")
_FUNDING_FIELDS = ("funding_source", "funder", "funded_by", "funding")
_PCT_FIELDS = ("pct", "percentage", "supply_pct", "percent")
_CLUSTER_FIELDS = ("cluster_id", "bundle_id", "cluster", "bundle", "group_id")

_EXCHANGE_CLASSES = {"cex", "exchange", "exchange_wallet"}
_FRESH_CLASSES = {"sniper", "bot", "fresh", "new_wallet"}


def _num(node: dict[str, Any], fields: tuple[str, ...]) -> float | None:
    for f in fields:
        v = node.get(f)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                continue
    return None


def _str(node: dict[str, Any], fields: tuple[str, ...]) -> str:
    for f in fields:
        v = node.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _holder_address(node: dict[str, Any]) -> str:
    return str(node.get("address") or node.get("id") or node.get("wallet") or "")


def _holder_pct(node: dict[str, Any]) -> float:
    return max(0.0, _num(node, _PCT_FIELDS) or 0.0)


def _wallet_age_days(node: dict[str, Any], label: dict[str, Any]) -> float | None:
    """Derive wallet age in days from node metadata or label record.

    Returns None when neither source carries age data — never guessed.
    """
    age = _num(node, _AGE_FIELDS)
    if age is not None:
        return age
    age = _num(label, _AGE_FIELDS)
    if age is not None:
        return age
    first_tx = _num(node, _FIRST_TX_FIELDS) or _num(label, _FIRST_TX_FIELDS)
    if first_tx and first_tx > 1e12:  # ms epoch
        first_tx /= 1000.0
    if first_tx and first_tx > 1e9:  # sane epoch seconds
        import time

        return max(0.0, (time.time() - first_tx) / 86400.0)
    return None


def _funding_source(node: dict[str, Any], label: dict[str, Any]) -> str:
    src = _str(node, _FUNDING_FIELDS) or _str(label, _FUNDING_FIELDS)
    if src:
        return src
    cls = str(label.get("class") or "").lower()
    if cls in _EXCHANGE_CLASSES:
        return "exchange"
    entity_type = str(label.get("entity_type") or label.get("category") or "").lower()
    if "exchange" in entity_type or "cex" in entity_type:
        return "exchange"
    return ""


async def _fetch_holders(token_address: str, chain: str, limit: int) -> list[dict[str, Any]]:
    """Fetch holder nodes from the bubble-map engine (bounded)."""
    try:
        from app.rugmaps_ai import get_token_holders

        nodes = await asyncio.wait_for(get_token_holders(token_address, chain), timeout=20)
        return [n for n in nodes if isinstance(n, dict)][:limit]
    except Exception as e:
        logger.debug(f"fresh-wallet holder fetch failed for {token_address[:12]}: {e}")
        return []


async def _fetch_labels(addresses: list[str], chain: str) -> dict[str, dict[str, Any]]:
    try:
        from app.rugmaps_wallet import batch_lookup_wallet_labels

        res = await batch_lookup_wallet_labels(addresses, chain=chain)
        return (res.get("labels") or {}) if isinstance(res, dict) else {}
    except Exception as e:
        logger.debug(f"fresh-wallet label fetch failed: {e}")
        return {}


def _group_holders(
    holders: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group holders into clusters by explicit cluster id, else shared
    funding source. Holders with neither stay singletons."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for h in holders:
        addr = _holder_address(h)
        label = labels.get(addr.lower(), {})
        key = _str(h, _CLUSTER_FIELDS) or _str(label, _CLUSTER_FIELDS)
        if not key:
            key = _funding_source(h, label)
        gid = f"cluster:{key}" if key else f"solo:{addr}"
        groups.setdefault(gid, []).append(h)
    return list(groups.values())


def _behavior_pattern(members: list[dict[str, Any]]) -> str:
    """Classify cluster behavior: sniper / wash_trade / hold / unknown.

    Uses buy/sell activity fields when present; never guesses.
    """
    buys = sum(float(m.get("buys") or m.get("buy_count") or 0) for m in members)
    sells = sum(float(m.get("sells") or m.get("sell_count") or 0) for m in members)
    tx_count = sum(float(m.get("transactions") or m.get("tx_count") or 0) for m in members)
    if tx_count == 0 and buys == 0 and sells == 0:
        return "unknown"
    if sells > 0 and buys / max(sells, 1) < 0.3:
        return "wash_trade"
    if buys > 0 and sells == 0:
        return "hold" if len(members) > 1 else "sniper"
    return "active"


@router.get("/fresh-wallet-analysis/{chain}/{address}")
async def fresh_wallet_analysis(
    chain: str,
    address: str,
    limit: int = Query(100, ge=10, le=300),
) -> dict[str, Any]:
    """Fresh-wallet supply analysis for a token.

    Returns the % of tracked supply held by wallets younger than 7 and 30
    days, the % funded by exchanges, and bundle detection (count + supply
    share). ``coverage_pct`` reports how much of the tracked supply had
    derivable age data — low coverage means the percentages are partial,
    not a clean bill of health.
    """
    holders = await _fetch_holders(address, chain, limit)
    if not holders:
        return {
            "token": address,
            "chain": chain,
            "fresh_wallet_pct_7d": None,
            "fresh_wallet_pct_30d": None,
            "exchange_funded_pct": None,
            "bundle_count": 0,
            "bundle_supply_pct": None,
            "coverage_pct": 0.0,
            "holder_count": 0,
            "source": "unavailable",
        }

    addresses = [_holder_address(h) for h in holders[:60]]
    labels = await _fetch_labels([a for a in addresses if a], chain)

    total_pct = sum(_holder_pct(h) for h in holders)
    fresh7 = fresh30 = exchange = aged_pct = 0.0
    fresh_wallets: list[dict[str, Any]] = []
    for h in holders:
        pct = _holder_pct(h)
        addr = _holder_address(h)
        label = labels.get(addr.lower(), {})
        age = _wallet_age_days(h, label)
        cls = str(label.get("class") or "").lower()
        if age is not None:
            aged_pct += pct
            if age < 7:
                fresh7 += pct
                fresh_wallets.append({"address": addr, "pct": pct, "age_days": round(age, 1)})
            elif age < 30:
                fresh30 += pct
                fresh_wallets.append({"address": addr, "pct": pct, "age_days": round(age, 1)})
        elif cls in _FRESH_CLASSES:
            # Labeled fresh-class wallet without explicit age — count as <30d only.
            fresh30 += pct
            fresh_wallets.append({"address": addr, "pct": pct, "age_days": None})
        if _funding_source(h, label) == "exchange" or cls in _EXCHANGE_CLASSES:
            exchange += pct

    groups = _group_holders(holders, labels)
    bundles = [g for g in groups if len(g) >= 3]
    bundle_supply = sum(_holder_pct(h) for g in bundles for h in g)

    return {
        "token": address,
        "chain": chain,
        "fresh_wallet_pct_7d": round(fresh7, 2) if aged_pct > 0 or fresh7 > 0 else None,
        "fresh_wallet_pct_30d": round(fresh30, 2) if aged_pct > 0 or fresh30 > 0 else None,
        "exchange_funded_pct": round(exchange, 2) if exchange > 0 else None,
        "bundle_count": len(bundles),
        "bundle_supply_pct": round(bundle_supply, 2) if bundles else None,
        "fresh_wallets": sorted(fresh_wallets, key=lambda w: -w["pct"])[:20],
        "coverage_pct": round(min(100.0, aged_pct), 2),
        "holder_count": len(holders),
        "tracked_supply_pct": round(total_pct, 2),
        "source": "holder_graph+wallet_labels",
    }


@router.get("/cluster-depth/{chain}/{address}")
async def cluster_depth(
    chain: str,
    address: str,
    limit: int = Query(100, ge=10, le=300),
    min_members: int = Query(2, ge=2, le=50),
) -> dict[str, Any]:
    """Deep cluster analysis — beyond BubbleMaps.

    Groups holders into clusters (explicit cluster ids, else shared funding
    source) and reports per-cluster member count, supply share, funding
    source, and behavior pattern (sniper / wash_trade / hold). Singletons
    are excluded; empty clusters list means no coordinated grouping was
    derivable — never fabricated.
    """
    holders = await _fetch_holders(address, chain, limit)
    if not holders:
        return {
            "token": address,
            "chain": chain,
            "clusters": [],
            "cluster_count": 0,
            "clustered_supply_pct": None,
            "source": "unavailable",
        }

    addresses = [_holder_address(h) for h in holders[:60]]
    labels = await _fetch_labels([a for a in addresses if a], chain)

    clusters: list[dict[str, Any]] = []
    for group in _group_holders(holders, labels):
        if len(group) < min_members:
            continue
        members: list[dict[str, Any]] = []
        funding_counts: dict[str, int] = {}
        for h in group:
            addr = _holder_address(h)
            label = labels.get(addr.lower(), {})
            fs = _funding_source(h, label) or "unknown"
            funding_counts[fs] = funding_counts.get(fs, 0) + 1
            members.append(
                {
                    "address": addr,
                    "pct": _holder_pct(h),
                    "label": label.get("label") or h.get("label"),
                    "class": label.get("class"),
                    "risk_score": label.get("risk_score"),
                }
            )
        funding_source = max(funding_counts, key=lambda k: funding_counts[k])
        clusters.append(
            {
                "id": f"cluster_{len(clusters) + 1}",
                "member_count": len(members),
                "supply_pct": round(sum(_holder_pct(h) for h in group), 2),
                "funding_source": funding_source,
                "funding_sources": funding_counts,
                "behavior_pattern": _behavior_pattern(group),
                "members": sorted(members, key=lambda m: -m["pct"])[:20],
            }
        )

    clusters.sort(key=lambda c: -c["supply_pct"])
    clustered_supply = sum(c["supply_pct"] for c in clusters)

    return {
        "token": address,
        "chain": chain,
        "clusters": clusters[:25],
        "cluster_count": len(clusters),
        "clustered_supply_pct": round(clustered_supply, 2) if clusters else None,
        "holder_count": len(holders),
        "source": "holder_graph+wallet_labels",
    }
