"""
Own Wallet Map — First-20 Buyer/Holder Role Classification
============================================================
Competitor feature for the deep scan: classifies the first ~20 buyers /
holders of a token into emoji roles so the report can show at a glance
who owns the early supply:

  - 🛠️  Dev      — the deployer wallet itself
  - 🐳  Whale    — large holder (>= 5% of tracked supply)
  - 🐟  Fish     — medium holder (1%..5%)
  - 🍤  Shrimp   — small holder (< 1%)
  - 🌱  Fresh    — brand-new wallet (first on-chain tx < 7 days ago)
  - 📦  Bundle   — wallet sharing its first funder with >= 1 other holder

Classification uses holder size (balance %), buy-time (first on-chain
tx timestamp), and funding source (first funder). Data comes from our
own ClickHouse first (holder_balances / evm_transfers /
solana_spl_balance_changes via ``_funding_queries``), with a Birdeye
holder-distribution fallback when ClickHouse has no record.

Fail-open by design: returns ``wallet_map_line: "—"`` when holder /
funding data is not reachable. Never fabricates roles for missing data.

Usage:
    from app.domains.scanner.engines.own_wallet_map import get_own_wallet_map

    result = await get_own_wallet_map("0x...", "ethereum", deployer="0x...")
    # result = {
    #     "check": "wallet_map",
    #     "result": "scanned",
    #     "wallet_map_line": "🛠️2 🐳1 🐟8 🍤7 🌱1 📦1",
    #     "roles": {"dev": 2, "whale": 1, "fish": 8, "shrimp": 7, "fresh": 1, "bundle": 1},
    #     "classified": [...],
    # }

Data Sources:
  Primary: wallet_memory.holder_balances / evm_transfers /
           solana_spl_balance_changes (via _funding_queries)
  Fallback: Birdeye token overview holder distribution
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from app.domains.scanner.engines._funding_queries import (
    _query_first_funders,
    _query_holder_first_tx,
    _query_top_holders,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_WALLET_MAP_CACHE: dict[str, dict[str, Any]] = {}
_WALLET_MAP_CACHE_TTL = 300  # 5 minutes — holder data changes slowly
_WALLET_MAP_CACHE_MAX = 100

# ── Role definitions ──────────────────────────────────────────────────────
ROLE_EMOJI: dict[str, str] = {
    "dev": "\U0001f6e0\ufe0f",  # 🛠️
    "whale": "\U0001f433",  # 🐳
    "fish": "\U0001f41f",  # 🐟
    "shrimp": "\U0001f964",  # 🍤
    "fresh": "\U0001f331",  # 🌱
    "bundle": "\U0001f4e6",  # 📦
}
_ROLE_ORDER: tuple[str, ...] = ("dev", "whale", "fish", "shrimp", "fresh", "bundle")

#: A holder whose wallet was born (first on-chain tx) within this window is "fresh".
_FRESH_WALLET_DAYS = 7
#: Whale / fish size thresholds (percentage of tracked supply).
_WHALE_PCT = 5.0
_FISH_PCT = 1.0
#: Max holders classified (the "first 20").
_MAX_HOLDERS = 20

#: DEX router / aggregator contracts that routinely appear as a holder's first
#: funder (the buy went through the router). They are infrastructure, not
#: evidence of a coordinated bundle — a shared router funder must never
#: classify holders as 📦. Lowercased (matches ``_enrich_holders`` output).
_KNOWN_ROUTER_WALLETS: frozenset[str] = frozenset(
    {
        # Uniswap V2 Router 02 (Ethereum)
        "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
        # Uniswap V3 SwapRouter / SwapRouter02 (Ethereum, Arbitrum, ...)
        "0xe592427a0aece92de3edee1f18e0157c05861564",
        "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
        # SwapRouter02 (Base)
        "0x2626664c2603336e57b271c5c0b26f421741e481",
        # SushiSwap Router
        "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",
        # PancakeSwap V2 Router / V3 SwapRouter (BSC)
        "0x10ed43c718714eb63d5aa57b78b54704e256024e",
        "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",
        # 1inch aggregator routers (v4 / v5-v6)
        "0x1111111254fb6c44bac0bed2854e76f90643097d",
        "0x1111111254eeb25477b68fb85ed929f73a960582",
        # 0x (ZRX) Exchange Proxy
        "0xdef1c0ded9bec7f1a1670819833240f027b25eff",
        # Quickswap V2 / V3 (Polygon)
        "0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff",
        "0xf5b509bb0909a69b1c207e495f45e8c9d590a6e9",
        # Camelot Router (Arbitrum)
        "0xc873fecbd354f5a56e00e710b90ef4201db2448d",
        # Solana: Jupiter v6 / Raydium (AmmV4 / CLMM) / Pump.fun programs
        "jup6lkzbzsj1jkkwapdhny74zcz3tluzoi5qnyvtav4",
        "675kpx9mhtjs2zt1qfr1nyhuzlexfqm9h24wfsut1mp8",
        "cammczo5yl8w4vff8kvhrk22ggusp5vta7grrkgrwqk",
        "6ef8rrecthr5dkzon8nwu78hrvfckubj14m5ubewf6p",
    }
)


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to the format used in ClickHouse tables."""
    return (chain or "").lower().strip()


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_wallet_map(
    token_address: str,
    chain: str,
    deployer: str | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Classify the first ~20 buyers/holders into emoji roles.

    Args:
        token_address: Token address (lowercased internally).
        chain: Chain name (ethereum / bsc / solana / ...).
        deployer: Optional deployer address — matched for the 🛠️ Dev role.
        force_fresh: Bypass the in-process cache.

    Returns:
        Dict with ``wallet_map_line`` (compact emoji map) plus the role
        counts and per-holder classification. Fail-open to ``"—"`` when
        holder / funding data is not reachable.
    """
    token_address = (token_address or "").lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"{token_address}:{chain_norm}:{(deployer or '').lower()}"
    if not force_fresh and cache_key in _WALLET_MAP_CACHE:
        cached = _WALLET_MAP_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _WALLET_MAP_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "wallet_map",
        "result": "unavailable",
        "wallet_map_line": "\u2014",  # —
        "roles": dict.fromkeys(_ROLE_ORDER, 0),
        "classified": [],
        "confidence": 0.0,
    }

    try:
        holders = await _fetch_holders(token_address, chain_norm)
    except Exception as exc:
        logger.debug("wallet_map_holder_fetch_failed: %s", exc)
        holders = []

    if not holders:
        logger.debug("no holder data for %s", token_address[:12])
        return result

    # ── Enrich: buy-time (wallet birth) + funding source ──
    try:
        first_tx, funders = await _enrich_holders([h["address"] for h in holders], chain_norm)
    except Exception as exc:
        logger.debug("wallet_map_enrich_failed: %s", exc)
        first_tx, funders = {}, {}

    for h in holders:
        addr = h["address"]
        h["first_tx_ts"] = first_tx.get(addr)
        h["funder"] = funders.get(addr)

    classified = _classify_holders(holders, deployer=deployer)
    roles = dict.fromkeys(_ROLE_ORDER, 0)
    for entry in classified:
        roles[entry["role"]] += 1

    result.update(
        {
            "result": "scanned",
            "wallet_map_line": _build_map_line(roles),
            "roles": roles,
            "classified": classified,
            "confidence": 0.8,
        }
    )

    _cache_map(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_wallet_map_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:12],
        chain=chain_norm,
        line=result["wallet_map_line"],
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# DATA ACCESS
# ═══════════════════════════════════════════════════════════════════════════


async def _fetch_holders(token_address: str, chain_norm: str) -> list[dict[str, Any]]:
    """Top-N holder list: ClickHouse first, Birdeye distribution fallback.

    Returns a list of ``{"address", "balance", "percentage"}`` dicts sorted
    by balance descending (max ``_MAX_HOLDERS``). Empty list when no data.
    """
    holders: list[dict[str, Any]] = []

    try:
        rows = await run_sync(_query_top_holders, token_address, chain_norm, _MAX_HOLDERS)
        if rows:
            total = sum(float(r[1] or 0) for r in rows)
            if total > 0:
                for addr, balance in rows:
                    addr_s = str(addr or "").lower()
                    if not addr_s:
                        continue
                    holders.append(
                        {
                            "address": addr_s,
                            "balance": float(balance or 0),
                            "percentage": round(float(balance or 0) / total * 100, 2),
                        }
                    )
    except Exception as exc:
        logger.debug("wallet_map_top_holders_failed: %s", exc)

    if holders:
        return holders[:_MAX_HOLDERS]

    # ── Fallback: Birdeye token overview holder distribution ──
    return await _fetch_birdeye_holders(token_address)


async def _fetch_birdeye_holders(token_address: str) -> list[dict[str, Any]]:
    """Birdeye token-overview holder distribution (graceful fallback)."""
    try:
        from app.birdeye_client import BirdeyeClient

        client = BirdeyeClient()
        try:
            overview = await client.get_token_overview(token_address)
        finally:
            await client.close()

        if not isinstance(overview, dict):
            return []
        nested = overview.get("data")
        data: dict[str, Any] = nested if isinstance(nested, dict) else overview
        dist = data.get("holderDistribution") or []
        total_supply = float(data.get("totalSupply", 1) or 1)

        holders: list[dict[str, Any]] = []
        for entry in dist:
            addr = str(entry.get("address") or entry.get("owner") or "").lower()
            if not addr:
                continue
            balance = float(entry.get("amount", entry.get("balance", 0)) or 0)
            pct = round(balance / total_supply * 100, 2) if total_supply else 0.0
            holders.append({"address": addr, "balance": balance, "percentage": pct})
        return holders[:_MAX_HOLDERS]
    except Exception as exc:
        logger.debug("wallet_map_birdeye_holders_failed: %s", exc)
        return []


async def _enrich_holders(holder_addrs: list[str], chain_norm: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Buy-time (first on-chain tx) + funding source per holder.

    Returns ``(first_tx_map, funder_map)`` keyed by holder address. Both
    degrade to ``{}`` when ClickHouse is unreachable or returns nothing.
    """
    first_tx_map: dict[str, Any] = {}
    funder_map: dict[str, Any] = {}

    if not holder_addrs or not chain_norm:
        return first_tx_map, funder_map

    try:
        first_tx_rows = await run_sync(_query_holder_first_tx, holder_addrs, chain_norm)
        for row in first_tx_rows or []:
            if len(row) >= 2 and row[0]:
                first_tx_map[str(row[0]).lower()] = row[1]
    except Exception as exc:
        logger.debug("wallet_map_first_tx_failed: %s", exc)

    try:
        funder_rows = await run_sync(_query_first_funders, holder_addrs, chain_norm)
        for row in funder_rows or []:
            if len(row) >= 2 and row[0]:
                funder_map[str(row[0]).lower()] = str(row[1] or "").lower()
    except Exception as exc:
        logger.debug("wallet_map_funders_failed: %s", exc)

    return first_tx_map, funder_map


# ═══════════════════════════════════════════════════════════════════════════
# CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════


def _classify_holders(holders: list[dict[str, Any]], deployer: str | None = None) -> list[dict[str, Any]]:
    """Assign exactly one role per holder (first match wins by precedence).

    Precedence: Dev > Bundle > Fresh > Whale > Fish > Shrimp. "Fresh" only
    applies to sub-whale holders — a brand-new 6% wallet is still a whale.
    """
    deployer_l = (deployer or "").lower().strip()
    bundle_funders = _bundle_funders(holders)
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(days=_FRESH_WALLET_DAYS)

    classified: list[dict[str, Any]] = []
    for h in holders:
        addr = h["address"]
        pct = float(h.get("percentage", 0) or 0)
        first_ts = h.get("first_tx_ts")
        funder = h.get("funder")

        if deployer_l and addr == deployer_l:
            role = "dev"
        elif funder and funder in bundle_funders:
            role = "bundle"
        elif first_ts is not None and _is_fresh(first_ts, cutoff) and pct < _WHALE_PCT:
            role = "fresh"
        elif pct >= _WHALE_PCT:
            role = "whale"
        elif pct >= _FISH_PCT:
            role = "fish"
        else:
            role = "shrimp"

        classified.append(
            {
                "address": addr,
                "role": role,
                "percentage": pct,
                "first_tx_ts": _to_iso(first_ts),
                "funder": funder,
                "emoji": ROLE_EMOJI[role],
            }
        )
    return classified


def _bundle_funders(holders: list[dict[str, Any]]) -> set[str]:
    """Funding sources shared by >= 2 of the classified holders.

    Known DEX routers/aggregators are excluded: when several holders bought
    through the same router, it shows up as everyone's first funder without
    implying a coordinated bundle.
    """
    counts: dict[str, int] = {}
    for h in holders:
        funder = h.get("funder")
        if funder and funder not in _KNOWN_ROUTER_WALLETS:
            counts[funder] = counts.get(funder, 0) + 1
    return {funder for funder, n in counts.items() if n >= 2}


def _is_fresh(first_ts: Any, cutoff: datetime) -> bool:
    """True when the wallet's first on-chain tx is newer than ``cutoff``."""
    if isinstance(first_ts, datetime):
        naive = first_ts.replace(tzinfo=None) if first_ts.tzinfo else first_ts
        return naive > cutoff
    try:
        parsed = datetime.fromisoformat(str(first_ts).replace("Z", "+00:00"))
        naive = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        return naive > cutoff
    except (TypeError, ValueError):
        return False


def _to_iso(value: Any) -> str | None:
    """ISO string for datetime values, else None (graceful)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _build_map_line(roles: dict[str, int]) -> str:
    """Compact emoji map line, e.g. ``🛠️2 🐳1 🐟8 🍤7 🌱1 📦1``."""
    parts = [f"{ROLE_EMOJI[role]}{roles.get(role, 0)}" for role in _ROLE_ORDER]
    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_map(key: str, data: dict[str, Any]) -> None:
    """Cache wallet map data with timestamp."""
    data["_cached_at"] = time.time()
    _WALLET_MAP_CACHE[key] = data
    if len(_WALLET_MAP_CACHE) > _WALLET_MAP_CACHE_MAX:
        _WALLET_MAP_CACHE.pop(next(iter(_WALLET_MAP_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _WALLET_MAP_CACHE,
        _WALLET_MAP_CACHE_MAX,
        _WALLET_MAP_CACHE_TTL,
        extra={"max_holders": _MAX_HOLDERS},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_WALLET_MAP_CACHE)
