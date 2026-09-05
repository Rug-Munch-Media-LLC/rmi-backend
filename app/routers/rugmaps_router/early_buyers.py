"""Early-buyers & bundle-timeline analysis routes:
/early-buyers/{chain}/{address}, /bundle-timeline/{chain}/{address}.

Same doctrine as ``analysis.py``: GET, deterministic, never fabricate data.
When a signal cannot be derived from our trade history / bundle detector /
wallet-label stack, the field reports ``null`` / ``"unknown"`` and
``coverage`` states how much of the derivation actually saw.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from typing import Any

from fastapi import Query

from app.core.logging import get_logger
from app.routers.rugmaps_router._helpers import sub_router
from app.routers.rugmaps_router.analysis import (
    _fetch_holders,
    _fetch_labels,
    _funding_source,
    _holder_address,
    _holder_pct,
    _num,
    _wallet_age_days,
)

logger = get_logger(__name__)

router = sub_router()

# Approximate block times (seconds) per chain — used ONLY to translate a
# block delta into a time window for sniper/bot classification. Documented
# heuristic, not fabricated per-token data.
_BLOCK_TIME_S = {
    "solana": 0.4,
    "ethereum": 12.0,
    "base": 2.0,
    "bsc": 3.0,
    "arbitrum": 0.25,
    "optimism": 2.0,
    "polygon": 2.0,
}

_EXCHANGE_CLASSES = {"cex", "exchange", "exchange_wallet"}


async def _fetch_buy_trades(token_address: str, chain: str, limit: int) -> list[dict[str, Any]]:
    """Fetch buy-side trades (earliest first) from Birdeye via our client.

    Returns a normalized list: maker, timestamp (unix s), price_usd,
    amount_token, amount_usd. Empty list when the source is unavailable —
    never synthesized.
    """
    try:
        from app.birdeye_client import BirdeyeClient

        client = BirdeyeClient()
        res = await asyncio.wait_for(
            client._call("/defi/v2/token/trades", {"address": token_address, "limit": limit, "offset": 0}),
            timeout=20,
        )
        items = (res.get("data") or {}).get("items") or [] if isinstance(res, dict) else []
        trades: list[dict[str, Any]] = []
        for t in items:
            if not isinstance(t, dict):
                continue
            side = str(t.get("side", "")).lower()
            if side not in ("buy", "b"):
                continue
            ts = t.get("blockUnixTime") or t.get("timestamp")
            try:
                ts = float(ts) if ts is not None else None
            except (TypeError, ValueError):
                ts = None
            trades.append(
                {
                    "maker": str(t.get("from") or t.get("maker") or t.get("wallet") or ""),
                    "timestamp": ts,
                    "price_usd": _num(t, ("priceUsd", "price")) or 0.0,
                    "amount_token": _num(t, ("amount", "tokenAmount")) or 0.0,
                    "amount_usd": _num(t, ("volumeUSD", "valueUsd")) or 0.0,
                    "tx_hash": str(t.get("txHash") or t.get("signature") or ""),
                }
            )
        trades.sort(key=lambda t: t["timestamp"] or 0)
        return trades
    except Exception as e:
        logger.debug(f"early-buyers trade fetch failed for {token_address[:12]}: {e}")
        return []


async def _fetch_bundle_report(token_address: str, chain: str) -> Any | None:
    """Fetch the BundleDetector report (same-block snipers, deploy time)."""
    try:
        from app.domains.scanners.bundle_detector import BundleDetector

        detector = BundleDetector()
        try:
            return await asyncio.wait_for(detector.detect_bundles(token_address, chain), timeout=30)
        finally:
            await detector.close()
    except Exception as e:
        logger.debug(f"bundle report fetch failed for {token_address[:12]}: {e}")
        return None


async def _fetch_current_price(token_address: str, chain: str) -> dict[str, float]:
    """Current price + estimated circulating supply from DexScreener."""
    try:
        from app.routers.rugmaps_router._helpers import _fetch_dexscreener_token_info

        pair = await _fetch_dexscreener_token_info(token_address, chain)
        if not pair:
            return {}
        price = float(pair.get("priceUsd") or 0)
        fdv = float(pair.get("fdv") or 0)
        supply = fdv / price if price > 0 and fdv > 0 else 0.0
        return {"price_usd": price, "supply_est": supply}
    except Exception as e:
        logger.debug(f"current price fetch failed for {token_address[:12]}: {e}")
        return {}


def _classify_buyer(
    buy_ts: float | None,
    launch_ts: float | None,
    block_delta: int | None,
    block_time: float,
) -> str:
    """Classify: sniper (first block) / bot (within 3 blocks) / early
    (within 1h) / normal. Block delta wins when known; otherwise the time
    delta vs launch is used with the chain's block time as proxy."""
    if block_delta is not None and launch_ts is not None:
        if block_delta <= 1:
            return "sniper"
        if block_delta <= 3:
            return "bot"
    if buy_ts is not None and launch_ts is not None:
        delta = buy_ts - launch_ts
        if delta <= block_time:
            return "sniper"
        if delta <= 3 * block_time:
            return "bot"
        if delta <= 3600:
            return "early"
        return "normal"
    if buy_ts is not None and launch_ts is None:
        # No launch reference — only the 1h "early" bucket is derivable
        # relative to the earliest observed buy (set by the caller).
        return "early" if buy_ts <= 3600 else "normal"
    return "unknown"


@router.get("/early-buyers/{chain}/{address}")
async def early_buyers(
    chain: str,
    address: str,
    limit: int = Query(50, ge=10, le=100),
) -> dict[str, Any]:
    """First-buyer forensics — who got in first and how they're doing.

    Returns the earliest buy-side trades (deduped per wallet), each with
    buy timestamp/price/amount/USD value, funding source, current holdings,
    estimated P&L, hold time, and wallet age at time of buy. Buyers are
    classified: sniper (first block), bot (within 3 blocks), early (within
    1 hour), normal. Fields that cannot be derived report ``null`` —
    never guessed. ``coverage`` states how much of the picture is real.
    """
    chain_key = chain.lower()
    block_time = _BLOCK_TIME_S.get(chain_key, 12.0)

    trades, bundle_report, price_info = await asyncio.gather(
        _fetch_buy_trades(address, chain_key, limit * 4),
        _fetch_bundle_report(address, chain_key),
        _fetch_current_price(address, chain_key),
    )
    if not trades:
        return {
            "token": address,
            "chain": chain,
            "buyers": [],
            "buyer_count": 0,
            "sniper_pct": None,
            "bot_pct": None,
            "fresh_wallet_pct": None,
            "median_hold_hours": None,
            "coverage_pct": 0.0,
            "source": "unavailable",
        }

    # Dedupe per wallet — keep each wallet's FIRST buy.
    first_buy: dict[str, dict[str, Any]] = {}
    for t in trades:
        maker = t["maker"]
        if maker and maker not in first_buy:
            first_buy[maker] = t
    buyers_raw = sorted(first_buy.values(), key=lambda t: t["timestamp"] or 0)[:limit]

    # Launch reference: bundle detector deploy timestamp, else None.
    launch_ts: float | None = None
    sniper_addrs: set[str] = set()
    if bundle_report is not None:
        launch_ts = float(getattr(bundle_report, "deploy_timestamp", 0) or 0) / 1000.0 or None
        for s in getattr(bundle_report, "same_block_snipers", []) or []:
            sniper_addrs.add(str(getattr(s, "address", "")))

    # If no launch timestamp, classify "early" relative to earliest observed buy.
    earliest_buy = min((t["timestamp"] for t in buyers_raw if t["timestamp"]), default=None)
    rel_launch = earliest_buy if launch_ts is None and earliest_buy is not None else None

    addresses = [b["maker"] for b in buyers_raw]
    labels = await _fetch_labels([a for a in addresses if a], chain_key)
    holders = await _fetch_holders(address, chain_key, 300)
    holdings: dict[str, float] = {}
    for h in holders:
        addr = _holder_address(h)
        if addr:
            holdings[addr.lower()] = _holder_pct(h)

    now_ts = time.time()

    buyers: list[dict[str, Any]] = []
    classified = 0
    aged = 0
    with_value = 0
    hold_hours: list[float] = []
    type_counts = {"sniper": 0, "bot": 0, "early": 0, "normal": 0}
    fresh_count = 0

    for b in buyers_raw:
        addr = b["maker"]
        label = labels.get(addr.lower(), {})
        buy_ts = b["timestamp"]
        buy_price = b["price_usd"] or None
        amount = b["amount_token"] or None
        usd = b["amount_usd"] or None

        # Current holdings: pct of tracked supply → token amount via supply est.
        pct = holdings.get(addr.lower())
        supply = price_info.get("supply_est") or 0.0
        cur_amount = (pct / 100.0 * supply) if pct is not None and supply > 0 else None
        cur_price = price_info.get("price_usd") or None
        cur_value = cur_amount * cur_price if cur_amount is not None and cur_price else None

        # P&L: current value vs buy USD value (both must be real).
        pnl = cur_value - usd if cur_value is not None and usd is not None else None

        # Hold time: time since first buy (still holding) — not exit time.
        hold_h = (now_ts - buy_ts) / 3600.0 if buy_ts else None
        if hold_h is not None:
            hold_hours.append(hold_h)

        # Wallet age at time of buy.
        age_now = _wallet_age_days({}, label)
        age_at_buy = None
        if age_now is not None and buy_ts:
            age_at_buy = max(0.0, age_now - (now_ts - buy_ts) / 86400.0)

        # Classification. Birdeye trades carry no block numbers, so the
        # block-based sniper/bot buckets only apply to bundle-detected
        # snipers; everyone else falls back to the time delta vs launch.
        if addr in sniper_addrs:
            wtype = "sniper"
        else:
            ref = launch_ts if launch_ts is not None else rel_launch
            if ref is not None and buy_ts is not None:
                wtype = _classify_buyer(buy_ts, ref, None, block_time)
                # Relative-to-earliest-buy fallback can only say early/normal.
                if launch_ts is None and wtype in ("sniper", "bot"):
                    wtype = "early"
            else:
                wtype = "unknown"
        if wtype in type_counts:
            type_counts[wtype] += 1
            classified += 1
        if age_at_buy is not None and age_at_buy < 7:
            fresh_count += 1

        funding = _funding_source({}, label) or "unknown"
        buyers.append(
            {
                "rank": 0,
                "address": addr,
                "buy_timestamp": buy_ts,
                "buy_price_usd": buy_price,
                "amount": amount,
                "usd_value": usd,
                "funding_source": funding,
                "current_holding_pct": round(pct, 4) if pct is not None else None,
                "current_value_usd": round(cur_value, 2) if cur_value is not None else None,
                "pnl_usd": round(pnl, 2) if pnl is not None else None,
                "hold_hours": round(hold_h, 2) if hold_h is not None else None,
                "wallet_age_days_at_buy": round(age_at_buy, 2) if age_at_buy is not None else None,
                "wallet_type": wtype,
                "is_fresh_wallet": (age_at_buy is not None and age_at_buy < 7) or None,
            }
        )
        if buy_price is not None and usd is not None:
            with_value += 1
        if age_now is not None:
            aged += 1

    for i, b in enumerate(buyers, 1):
        b["rank"] = i

    n = len(buyers)
    return {
        "token": address,
        "chain": chain,
        "buyers": buyers,
        "buyer_count": n,
        "sniper_pct": round(type_counts["sniper"] / n * 100, 2) if n else None,
        "bot_pct": round(type_counts["bot"] / n * 100, 2) if n else None,
        "fresh_wallet_pct": round(fresh_count / n * 100, 2) if n else None,
        "median_hold_hours": round(statistics.median(hold_hours), 2) if hold_hours else None,
        "launch_timestamp": launch_ts,
        "current_price_usd": price_info.get("price_usd"),
        "coverage_pct": round(
            100.0 * (classified / n) * 0.5 + 100.0 * (aged / n) * 0.25 + 100.0 * (with_value / n) * 0.25,
            2,
        )
        if n
        else 0.0,
        "source": "birdeye_trades+bundle_detector+wallet_labels+holder_graph",
    }


@router.get("/bundle-timeline/{chain}/{address}")
async def bundle_timeline(chain: str, address: str) -> dict[str, Any]:
    """Timeline of bundle creation events for a token.

    Groups the bundle detector's funded wallets and same-block snipers by
    block into creation events: block number, timestamp, wallet count,
    supply % bought, and funding source. Shows the pattern — e.g. "3
    bundles created in block X, funded by exchange Y, bought Z% of
    supply". Empty timeline means no coordinated bundling was derivable —
    never fabricated.
    """
    chain_key = chain.lower()
    block_time = _BLOCK_TIME_S.get(chain_key, 12.0)

    bundle_report, price_info = await asyncio.gather(
        _fetch_bundle_report(address, chain_key),
        _fetch_current_price(address, chain_key),
    )
    if bundle_report is None:
        return {
            "token": address,
            "chain": chain,
            "events": [],
            "event_count": 0,
            "total_bundled_supply_pct": None,
            "source": "unavailable",
        }

    deploy_block = int(getattr(bundle_report, "deploy_block", 0) or 0)
    deploy_ts_ms = float(getattr(bundle_report, "deploy_timestamp", 0) or 0)
    supply = price_info.get("supply_est") or 0.0

    # Collect (block, address, amount) entries from funded wallets + snipers.
    entries: list[tuple[int, str, float]] = []
    for w in getattr(bundle_report, "deployer_funded", []) or []:
        addr = str(getattr(w, "address", ""))
        block = int(getattr(w, "buy_block", 0) or 0)
        amount = float(getattr(w, "buy_amount", 0) or 0)
        if addr:
            entries.append((block, addr, amount))
    for s in getattr(bundle_report, "same_block_snipers", []) or []:
        addr = str(getattr(s, "address", ""))
        block = int(getattr(s, "block_number", 0) or 0)
        amount = float(getattr(s, "buy_amount", 0) or 0)
        if addr:
            entries.append((block, addr, amount))

    if not entries:
        return {
            "token": address,
            "chain": chain,
            "events": [],
            "event_count": 0,
            "total_bundled_supply_pct": None,
            "deploy_block": deploy_block or None,
            "source": "bundle_detector",
        }

    addresses = [e[1] for e in entries]
    labels = await _fetch_labels(addresses, chain_key)

    # Group by block → bundle events.
    by_block: dict[int, list[tuple[str, float]]] = {}
    for block, addr, amount in entries:
        by_block.setdefault(block, []).append((addr, amount))

    events: list[dict[str, Any]] = []
    for block in sorted(by_block):
        members = by_block[block]
        funding_counts: dict[str, int] = {}
        for addr, _ in members:
            label = labels.get(addr.lower(), {})
            fs = _funding_source({}, label) or "unknown"
            funding_counts[fs] = funding_counts.get(fs, 0) + 1
        funding_source = max(funding_counts, key=lambda k: funding_counts[k])
        amount_sum = sum(a for _, a in members)
        supply_pct = (amount_sum / supply * 100) if supply > 0 and amount_sum > 0 else None
        # Timestamp: deploy time + block delta * chain block time (estimate).
        ts = None
        if deploy_ts_ms > 0 and deploy_block and block >= deploy_block:
            ts = deploy_ts_ms / 1000.0 + (block - deploy_block) * block_time
        events.append(
            {
                "block": block,
                "timestamp": round(ts, 1) if ts else None,
                "timestamp_estimated": ts is not None,
                "bundle_count": len(members),
                "wallet_count": len(members),
                "supply_pct": round(supply_pct, 2) if supply_pct else None,
                "funding_source": funding_source,
                "funding_sources": funding_counts,
                "wallets": [addr for addr, _ in members[:20]],
            }
        )

    total_pct = sum(e["supply_pct"] or 0 for e in events) or None
    return {
        "token": address,
        "chain": chain,
        "deploy_block": deploy_block or None,
        "deploy_timestamp": deploy_ts_ms / 1000.0 if deploy_ts_ms else None,
        "events": events,
        "event_count": len(events),
        "total_bundled_supply_pct": round(total_pct, 2) if total_pct else None,
        "risk_level": str(getattr(bundle_report, "risk_level", "") or "") or None,
        "source": "bundle_detector+wallet_labels",
    }
