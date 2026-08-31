"""
Prediction Market Insider Detection
====================================
Identifies wallets with consistently winning positions on prediction markets
(Polymarket first) and labels them "smart money" for the alpha product.

Logic adapted from the polymarket-insider-detector reference implementation
(lib/detection.js + lib/polymarketApi.js): binomial p-value vs a 50/50
null, timing analysis, and a composite suspicion score. Pure functions are
unit-testable without network; the fetchers are fail-open.

Data sources (public, no auth):
  - https://data-api.polymarket.com/trades   — recent trades (proxyWallet)
  - https://gamma-api.polymarket.com/markets — resolved markets (outcomes)
"""

from __future__ import annotations

import math
import time
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("prediction_market.insiders")

POLYMARKET_DATA_API = "https://data-api.polymarket.com"
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"

#: Smart-money thresholds (mission spec: win rate > 60%, > 5 positions).
MIN_WIN_RATE = 0.60
MIN_POSITIONS = 5

_CACHE_TTL_SECONDS = 300.0
_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL_SECONDS:
        return entry[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    # Bound the cache so long-running processes don't leak.
    if len(_cache) > 64:
        _cache.clear()
    _cache[key] = (time.monotonic(), value)


def binomial_p_value(wins: int, total: int, p: float = 0.5) -> float:
    """Exact binomial tail probability P(X >= wins) for X ~ Bin(total, p).

    Port of the detector's jStat-based binomialPValue — pure stdlib.
    """
    if total <= 0 or wins <= 0:
        return 1.0
    total_prob = 0.0
    for k in range(wins, total + 1):
        coeff = math.comb(total, k)
        total_prob += coeff * (p**k) * ((1 - p) ** (total - k))
    return min(1.0, max(0.0, total_prob))


def _parse_outcome(market: dict[str, Any]) -> str | None:
    """'Yes'/'No' winner from a Gamma market row (None when unresolved)."""
    try:
        prices = market.get("outcomePrices")
        if isinstance(prices, str):
            import json

            prices = json.loads(prices)
        if isinstance(prices, list) and len(prices) >= 2:
            if float(prices[0]) > 0.99:
                return "Yes"
            if float(prices[1]) > 0.99:
                return "No"
    except (ValueError, TypeError, ImportError):
        pass
    return None


def _parse_market(m: dict[str, Any]) -> dict[str, Any] | None:
    cid = m.get("conditionId") or m.get("id")
    if not cid:
        return None
    resolution_time = None
    for key in ("closedTime", "endDate", "end_date_iso"):
        raw = m.get(key)
        if raw:
            try:
                from datetime import datetime

                resolution_time = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
                break
            except ValueError:
                continue
    return {
        "id": cid,
        "question": m.get("question", ""),
        "slug": m.get("slug", ""),
        "resolution_time": resolution_time,
        "outcome": _parse_outcome(m),
        "volume": float(m.get("volumeNum") or 0),
    }


def _parse_trade(t: dict[str, Any], idx: int) -> dict[str, Any] | None:
    wallet = (t.get("proxyWallet") or t.get("wallet") or "").lower()
    if not wallet:
        return None
    try:
        return {
            "id": t.get("transactionHash") or f"trade_{idx}",
            "market_id": t.get("conditionId") or "",
            "wallet": wallet,
            "side": (t.get("side") or "buy").lower(),
            "amount": float(t.get("size") or 0),
            "price": float(t.get("price") or 0),
            "timestamp": float(t.get("timestamp") or 0),
            "outcome": t.get("outcome") or "",
            "username": t.get("pseudonym") or t.get("name") or "",
        }
    except (TypeError, ValueError):
        return None


def analyze_wallet(
    wallet: dict[str, Any], trades: list[dict[str, Any]], markets: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Analyze one wallet's resolved-position history (port of detection.js).

    Returns None when the wallet has fewer than MIN_POSITIONS resolved
    positions — not enough signal to label anyone.
    """
    wallet_trades = [t for t in trades if t.get("wallet") == wallet.get("address")]
    if len(wallet_trades) < MIN_POSITIONS:
        return None

    wins = 0
    losses = 0
    total_pnl = 0.0
    total_stake = 0.0
    timings_before: list[float] = []
    recent_bets: list[dict[str, Any]] = []

    for trade in wallet_trades:
        market = markets.get(trade.get("market_id") or "")
        if not market or not market.get("outcome"):
            continue
        bought_yes = trade.get("side") == "buy"
        outcome_yes = market["outcome"] == "Yes"
        won = bought_yes == outcome_yes
        amount = trade.get("amount", 0.0)
        price = trade.get("price", 0.0)
        if won:
            wins += 1
            total_pnl += amount * (1 - price)
        else:
            losses += 1
            total_pnl -= amount * price
        total_stake += amount * price
        if market.get("resolution_time") and trade.get("timestamp"):
            timings_before.append((market["resolution_time"] - trade["timestamp"]) / 60.0)
        if len(recent_bets) < 10:
            recent_bets.append(
                {
                    "market": market.get("question", ""),
                    "side": trade.get("side"),
                    "outcome": trade.get("outcome"),
                    "amount": amount,
                    "price": price,
                    "timestamp": trade.get("timestamp"),
                    "won": won,
                }
            )

    total = wins + losses
    if total < MIN_POSITIONS:
        return None

    win_rate = wins / total
    p_value = binomial_p_value(wins, total)
    avg_minutes_before = (sum(timings_before) / len(timings_before)) if timings_before else None
    last_minute_ratio = (sum(1 for t in timings_before if t <= 30) / len(timings_before)) if timings_before else 0.0

    # Composite suspicion score (same bands as the reference detector).
    suspicion = 0
    if p_value < 0.0001:
        suspicion += 4
    elif p_value < 0.001:
        suspicion += 3
    elif p_value < 0.01:
        suspicion += 2
    elif p_value < 0.05:
        suspicion += 1
    if win_rate > 0.9:
        suspicion += 2
    elif win_rate > 0.8:
        suspicion += 1
    if avg_minutes_before is not None:
        if avg_minutes_before < 10:
            suspicion += 3
        elif avg_minutes_before < 30:
            suspicion += 2
        elif avg_minutes_before < 60:
            suspicion += 1
    if last_minute_ratio > 0.8:
        suspicion += 2
    elif last_minute_ratio > 0.5:
        suspicion += 1
    if total_stake > 500_000:
        suspicion += 2
    elif total_stake > 100_000:
        suspicion += 1

    level = "low"
    if suspicion >= 10:
        level = "critical"
    elif suspicion >= 7:
        level = "high"
    elif suspicion >= 4:
        level = "medium"

    is_smart_money = win_rate > MIN_WIN_RATE and total >= MIN_POSITIONS

    return {
        "wallet_address": wallet.get("address"),
        "username": wallet.get("usernames", [None])[0] if wallet.get("usernames") else None,
        "win_rate": round(win_rate, 4),
        "total_positions": total,
        "wins": wins,
        "losses": losses,
        "profit": round(total_pnl, 2),
        "total_stake": round(total_stake, 2),
        "roi": round(total_pnl / total_stake, 4) if total_stake > 0 else 0.0,
        "p_value": p_value,
        "avg_minutes_before_resolution": round(avg_minutes_before, 1) if avg_minutes_before is not None else None,
        "last_minute_ratio": round(last_minute_ratio, 4),
        "suspicion_score": suspicion,
        "suspicion_level": level,
        "smart_money": is_smart_money,
        "recent_bets": recent_bets,
    }


def aggregate_by_wallet(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group trades by wallet (port of polymarketApi.js aggregateByWallet)."""
    wallet_map: dict[str, dict[str, Any]] = {}
    for trade in trades:
        wallet = trade.get("wallet")
        if not wallet:
            continue
        entry = wallet_map.setdefault(
            wallet, {"address": wallet, "trades": [], "total_volume": 0.0, "usernames": set()}
        )
        entry["trades"].append(trade)
        entry["total_volume"] += trade.get("amount", 0.0) * trade.get("price", 0.0)
        if trade.get("username"):
            entry["usernames"].add(trade["username"])
    return [{**w, "usernames": sorted(w["usernames"])} for w in wallet_map.values()]


async def fetch_polymarket_trades(client: httpx.AsyncClient, limit: int = 2000) -> list[dict[str, Any]]:
    """Recent trades from the public Polymarket data API (fail-open to [])."""
    trades: list[dict[str, Any]] = []
    offset = 0
    batch = 100
    while len(trades) < limit:
        try:
            resp = await client.get(f"{POLYMARKET_DATA_API}/trades", params={"limit": batch, "offset": offset})
            if resp.status_code != 200:
                break
            data = resp.json()
        except httpx.HTTPError:
            break
        if not data:
            break
        for i, t in enumerate(data):
            parsed = _parse_trade(t, len(trades) + i)
            if parsed:
                trades.append(parsed)
        offset += batch
        if len(data) < batch:
            break
    return trades[:limit]


async def fetch_resolved_markets(client: httpx.AsyncClient, limit: int = 500) -> dict[str, dict[str, Any]]:
    """Recently resolved Gamma markets keyed by conditionId (fail-open to {})."""
    markets: dict[str, dict[str, Any]] = {}
    offset = 0
    batch = 100
    while len(markets) < limit:
        try:
            resp = await client.get(
                f"{POLYMARKET_GAMMA}/markets",
                # Newest-resolved first: without an explicit order the Gamma
                # API returns the OLDEST closed markets (2021-era), which
                # never overlap recent trades → empty insider leaderboard.
                params={
                    "closed": "true",
                    "limit": batch,
                    "offset": offset,
                    "order": "closedTime",
                    "ascending": "false",
                },
            )
            if resp.status_code != 200:
                break
            data = resp.json()
        except httpx.HTTPError:
            break
        if not data:
            break
        for m in data:
            parsed = _parse_market(m)
            if parsed and parsed["outcome"]:
                markets[parsed["id"]] = parsed
        offset += batch
        if len(data) < batch:
            break
    return markets


async def get_prediction_market_insiders(
    min_win_rate: float = MIN_WIN_RATE,
    min_positions: int = MIN_POSITIONS,
    trade_limit: int = 2000,
    market_limit: int = 500,
) -> dict[str, Any]:
    """Full insider-detection pipeline: fetch → aggregate → analyze → rank.

    Cached for _CACHE_TTL_SECONDS. Fail-open: any fetch failure yields an
    empty leaderboard, never a raise.
    """
    cache_key = f"insiders:{min_win_rate}:{min_positions}:{trade_limit}:{market_limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=15.0) as client:
        trades, markets = await asyncio_gather_trades_markets(client, trade_limit, market_limit)

    wallets = aggregate_by_wallet(trades)
    analyzed = []
    for w in wallets:
        result = analyze_wallet(w, trades, markets)
        if result:
            analyzed.append(result)

    smart = [a for a in analyzed if a["smart_money"] and a["win_rate"] >= min_win_rate]
    smart.sort(key=lambda a: (-a["win_rate"], a["p_value"]))
    for rank, a in enumerate(smart, start=1):
        a["rank"] = rank
        a["label"] = "smart_money"

    result = {
        "total_wallets_analyzed": len(analyzed),
        "smart_money_count": len(smart),
        "thresholds": {"min_win_rate": min_win_rate, "min_positions": min_positions},
        "insiders": smart,
        "source": "polymarket",
    }
    _cache_set(cache_key, result)
    return result


async def asyncio_gather_trades_markets(
    client: httpx.AsyncClient, trade_limit: int, market_limit: int
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    import asyncio

    trades, markets = await asyncio.gather(
        fetch_polymarket_trades(client, trade_limit),
        fetch_resolved_markets(client, market_limit),
    )
    return trades, markets
