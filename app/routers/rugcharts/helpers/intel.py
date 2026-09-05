"""Intelligence/analysis for RugCharts: scoring, holder stats ladder, chain freshness."""

from __future__ import annotations

import importlib
from typing import Any

import httpx

from .common import CACHE_TTL, HELIUS_KEY, INTERNAL_API_BASE, logger


def _resolve(name: str) -> Any:
    """Resolve a helper by name from the package namespace at call time.

    Keeps monkeypatch-style overrides applied to the ``helpers`` package
    (see tests/test_rugcharts_wins.py) effective despite the module split.
    """
    return getattr(importlib.import_module(__package__), name)


# ── Scoring (pure) ───────────────────────────────────────────────────────


def _compute_volume_authenticity(pair_info: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute volume authenticity score from available data."""
    vol_24h = pair_info.get("volume_24h", 0)
    vol_6h = pair_info.get("volume_6h", 0)
    liq = pair_info.get("liquidity_usd", 0)
    buys = pair_info.get("txns_24h_buys", 0)
    sells = pair_info.get("txns_24h_sells", 0)
    total_txns = buys + sells

    score = 100
    risk_flags: list[str] = []

    if liq > 0 and vol_24h > 0:
        ratio = vol_24h / liq
        if ratio > 100:
            score -= 30
            risk_flags.append(f"Extreme vol/liq ratio ({ratio:.0f}x) - likely wash trading")
        elif ratio > 50:
            score -= 20
            risk_flags.append(f"Very high vol/liq ratio ({ratio:.0f}x)")
        elif ratio > 20:
            score -= 10
            risk_flags.append(f"Elevated vol/liq ratio ({ratio:.0f}x)")

    if vol_24h > 0 and vol_6h > 0:
        expected_6h = vol_24h * 0.25
        if vol_6h > expected_6h * 3:
            score -= 15
            risk_flags.append("Volume concentrated in recent 6h (burst pattern)")
        elif vol_6h < expected_6h * 0.1:
            score -= 10
            risk_flags.append("Almost no recent volume (dying token)")

    if total_txns > 0:
        buy_pct = buys / total_txns
        if buy_pct < 0.2:
            score -= 15
            risk_flags.append(f"Heavy sell dominance ({sells} sells vs {buys} buys)")
        elif buy_pct > 0.9:
            score -= 10
            risk_flags.append(f"Suspiciously high buy ratio ({buy_pct * 100:.0f}%) - possible bot activity")

    if len(candles) >= 3:
        volumes = [c["volume"] for c in candles]
        avg_vol = sum(volumes) / len(volumes)
        if avg_vol > 0:
            max_vol = max(volumes)
            if max_vol > avg_vol * 10:
                score -= 10
                risk_flags.append(f"Volume spike detected ({max_vol / avg_vol:.0f}x average)")
            if len(volumes) > 5:
                std_dev = (sum((v - avg_vol) ** 2 for v in volumes) / len(volumes)) ** 0.5
                cv = std_dev / avg_vol if avg_vol > 0 else 0
                if cv < 0.05:
                    score -= 15
                    risk_flags.append("Unnaturally uniform volume distribution (bot pattern)")

    if vol_24h == 0:
        score -= 40
        risk_flags.append("No 24h trading volume")

    score = max(0, min(100, score))
    risk_level = "LOW" if score >= 70 else "MEDIUM" if score >= 40 else "HIGH"

    return {
        "authentic_score": score,
        "fake_volume_pct": max(0, 100 - score),
        "risk_level": risk_level,
        "risk_flags": risk_flags,
        "metrics": {
            "volume_24h": vol_24h,
            "liquidity_usd": liq,
            "vol_liq_ratio": round(vol_24h / liq, 2) if liq > 0 else 0,
            "buy_count": buys,
            "sell_count": sells,
            "buy_sell_ratio": round(buys / sells, 2) if sells > 0 else 0,
        },
    }


def _compute_rug_score(pair_info: dict[str, Any], vol_auth: dict[str, Any]) -> dict[str, Any]:
    """Compute overall rug risk score."""
    score = 0
    factors: list[str] = []

    liq = pair_info.get("liquidity_usd", 0)
    fdv = pair_info.get("fdv", 0)
    change = pair_info.get("price_change_24h", 0)
    buys = pair_info.get("txns_24h_buys", 0)
    sells = pair_info.get("txns_24h_sells", 0)

    if liq < 1e4:
        score += 30
        factors.append("Critically low liquidity (<$10K)")
    elif liq < 5e4:
        score += 15
        factors.append("Low liquidity (<$50K)")
    elif liq > 1e6:
        score -= 5
        factors.append("Deep liquidity pool (>$1M)")

    if fdv > 0 and liq > 0:
        ratio = fdv / liq
        if ratio > 100:
            score += 20
            factors.append(f"Extreme FDV/Liq ratio ({ratio:.0f}x)")
        elif ratio > 20:
            score += 10
            factors.append(f"Elevated FDV/Liq ratio ({ratio:.0f}x)")

    if sells > buys * 1.5 and buys > 0:
        score += 15
        factors.append(f"Heavy sell pressure ({sells} sells vs {buys} buys)")
    elif buys > sells * 1.5 and sells > 0:
        score -= 10
        factors.append("Strong organic buy pressure")

    if change < -20:
        score += 20
        factors.append(f"Massive dump ({change:.1f}%)")
    elif change > 50:
        score += 8
        factors.append(f"Extreme pump (+{change:.1f}%) - potential exit liquidity")
    elif change > 10:
        score += 5
        factors.append("Rapid price increase")

    auth_score = vol_auth.get("authentic_score", 100)
    if auth_score < 50:
        score += 15
        factors.append(f"Low volume authenticity ({auth_score}/100)")

    created = pair_info.get("pool_created_at", "")
    if created:
        try:
            from datetime import datetime

            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_hours = (datetime.now(created_dt.tzinfo) - created_dt).total_seconds() / 3600
            if age_hours < 1:
                score += 15
                factors.append(f"Brand new pool ({age_hours:.1f}h old)")
            elif age_hours < 24:
                score += 8
                factors.append("New pool (<24h old)")
        except (ValueError, TypeError) as e:
            logger.debug(f"Pool age parse failed: {e}")

    score = max(0, min(100, score))
    level = "SAFE" if score < 30 else "CAUTION" if score < 60 else "DANGER"
    color = "#10b981" if score < 30 else "#f59e0b" if score < 60 else "#ef4444"

    return {"score": score, "level": level, "color": color, "factors": factors}


# ── Holder stats (own indexer first; provider ladder, never single-source) ──


async def _holder_stats_from_clickhouse(token_address: str, chain: str) -> dict[str, Any] | None:
    """Top-holder concentration from OUR indexer (wallet_memory.holder_balances).

    Works for every EVM chain the lite-chain-indexer covers (base, bsc,
    ethereum, polygon, arbitrum, optimism, avalanche, gnosis, tron, ...).
    Percentages are computed on raw integer balances so no decimals lookup
    is needed. Supply denominator = sum of indexed balances — an honest
    transfer-derived approximation, labeled as such.
    """
    token_l = (token_address or "").lower()
    if not token_l or not chain:
        return None
    try:
        top_rows = await _resolve("_ch_query")(
            "SELECT address, balance FROM holder_balances "
            "WHERE chain = %(chain)s AND lower(token_address) = %(token)s "
            "ORDER BY balance DESC LIMIT 20",
            {"chain": chain, "token": token_l},
        )
        if not top_rows:
            return None
        total_rows = await _resolve("_ch_query")(
            "SELECT sum(balance), count() FROM holder_balances "
            "WHERE chain = %(chain)s AND lower(token_address) = %(token)s",
            {"chain": chain, "token": token_l},
        )
        total_supply = float(total_rows[0][0]) if total_rows and total_rows[0][0] else 0.0
        n_accounts = int(total_rows[0][1]) if total_rows and total_rows[0][1] else 0
        if total_supply <= 0:
            return None
        balances = sorted((float(b) for _a, b in top_rows), reverse=True)
        return {
            "source": "chain_indexer.clickhouse",
            "top_holder_pct": round(balances[0] / total_supply * 100, 2),
            "top10_holder_pct": round(sum(balances[:10]) / total_supply * 100, 2),
            "sampled_accounts": n_accounts,
            "metric": "indexed_transfer_balances",
        }
    except Exception as e:
        logger.debug(f"CH holder stats failed {chain}/{token_address}: {e}")
        return None


async def _holder_stats_from_solana_transfers(token_address: str) -> dict[str, Any] | None:
    """Solana holder concentration from OUR spl balance-change index.

    Coverage is limited to mints the indexer has tracked so far; returns
    None quickly when the mint is unknown so callers fall through.
    """
    mint = (token_address or "").lower()
    if not mint:
        return None
    try:
        rows = await _resolve("_ch_query")(
            "SELECT owner, argMax(post_amount, slot) AS bal "
            "FROM solana_spl_balance_changes WHERE lower(mint) = %(mint)s "
            "AND owner != '' GROUP BY owner ORDER BY bal DESC LIMIT 20",
            {"mint": mint},
        )
        if len(rows) < 2:
            return None
        balances = []
        for _owner, bal in rows:
            try:
                balances.append(float(bal))
            except (TypeError, ValueError):
                continue
        balances = [b for b in balances if b > 0]
        total = sum(balances)
        if total <= 0:
            return None
        return {
            "source": "chain_indexer.solana_spl",
            "top_holder_pct": round(balances[0] / total * 100, 2),
            "top10_holder_pct": round(sum(balances[:10]) / total * 100, 2),
            "sampled_accounts": len(balances),
            "metric": "indexed_balance_changes",
        }
    except Exception as e:
        logger.debug(f"SPL holder stats failed {token_address}: {e}")
        return None


async def _holder_stats_from_scanner_api(chain: str, token_address: str) -> dict[str, Any] | None:
    """Fallback: our own multi-provider scanner service.

    Called over HTTP (not imported) so this package stays decoupled from
    backend internals — a standalone rugcharts deployment consumes the
    exact same surface by pointing RMI_INTERNAL_API_BASE at the platform.
    """
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{INTERNAL_API_BASE}/api/v2/scanner/token/holders/{chain}/{token_address}")
            if r.status_code != 200:
                return None
            d = r.json().get("data", {}) or {}
            if d.get("top_holder_pct") is None and d.get("top10_holders_pct") is None:
                return None
            return {
                "source": f"scanner_service.{d.get('provider', 'aggregated')}",
                "top_holder_pct": float(d.get("top_holder_pct") or 0),
                "top10_holder_pct": float(d.get("top10_holders_pct") or 0),
                "sampled_accounts": int(d.get("holder_count") or 0),
                "metric": "scanner_aggregated",
            }
    except Exception as e:
        logger.debug(f"Scanner holder fallback failed {chain}/{token_address}: {e}")
        return None


async def _holder_stats_from_helius(token_address: str) -> dict[str, Any] | None:
    """LAST-resort tier (Solana only): Helius getTokenLargestAccounts."""
    if not HELIUS_KEY or not token_address:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenLargestAccounts",
                    "params": [token_address],
                },
            )
            if r.status_code != 200:
                return None
            accounts = r.json().get("result", {}).get("value", [])
            supplies = [float(a.get("uiAmount") or 0) for a in accounts if a.get("uiAmount") is not None]
            total = sum(supplies)
            if total <= 0:
                return None
            top20 = sorted(supplies, reverse=True)[:20]
            return {
                "source": "helius",
                "top_holder_pct": round(top20[0] / total * 100, 2),
                "top10_holder_pct": round(sum(top20[:10]) / total * 100, 2),
                "sampled_accounts": len(accounts),
                "metric": "largest_accounts_concentration",
            }
    except Exception as e:
        logger.debug(f"Helius holder stats failed for {token_address}: {e}")
        return None


async def _fetch_holder_stats(token_address: str, chain: str) -> dict[str, Any] | None:
    """Holder concentration via a provider LADDER — own indexer first.

    Tier 1: ClickHouse holder_balances (all indexed EVM chains)
    Tier 2: ClickHouse solana SPL balance changes (our Solana coverage)
    Tier 3: internal multi-provider scanner service
    Tier 4: Helius (Solana only, last resort)

    Returns None ONLY when every tier fails — unknown stays unknown, never 0.
    """
    if not token_address or not chain:
        return None

    cache_key = f"rugcharts:holders:{chain}:{(token_address or '').lower()}"
    cached = await _resolve("_cache_get_json")(cache_key)
    if cached is not None:
        return cached

    stats: dict[str, Any] | None = None
    if chain == "solana":
        stats = (
            await _resolve("_holder_stats_from_solana_transfers")(token_address)
            or await _resolve("_holder_stats_from_scanner_api")(chain, token_address)
            or await _resolve("_holder_stats_from_helius")(token_address)
        )
    else:
        stats = await _resolve("_holder_stats_from_clickhouse")(token_address, chain) or (
            await _resolve("_holder_stats_from_scanner_api")(chain, token_address)
        )

    if stats:
        await _resolve("_cache_set_json")(cache_key, stats, CACHE_TTL)
    return stats


# ── Chain data freshness (ops visibility — audit WIN-9) ──────────────────


async def _chain_freshness() -> dict[str, dict[str, Any]]:
    """Per-chain freshness of our own indexer tables (dex_events).

    Makes coverage gaps like "no solana rows" or "bsc stale since Aug 9"
    visible to ops instead of silently returning empty overlays.
    """
    try:
        rows = await _resolve("_ch_query")(
            "SELECT chain, count(), max(block_timestamp), "
            "dateDiff('hour', max(block_timestamp), now()) "
            "FROM dex_events GROUP BY chain ORDER BY max(block_timestamp) DESC",
            {},
        )
    except Exception as e:
        logger.debug(f"freshness query failed: {e}")
        return {}

    out: dict[str, dict[str, Any]] = {}
    for chain, cnt, latest, hours in rows:
        hours = int(hours or 0)
        out[chain] = {
            "events": int(cnt),
            "latest_utc": str(latest),
            "hours_stale": hours,
            "status": "fresh" if hours <= 2 else "delayed" if hours <= 24 else "stale",
        }
    return out
