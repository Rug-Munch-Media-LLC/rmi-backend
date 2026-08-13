"""V1 launch feed endpoints — /api/v1/launches/*.

New-token-launch feed across all supported chains.

Data cascade (own data first, then external fallbacks):
  Tier 1 — ClickHouse ``deployer_events`` (our own chain indexer)
  Tier 2 — DexScreener new pairs (free, no key)
  Tier 3 — GMGN pump.fun new_creation / bonded pairs (free, no key)

Optional pump.fun filter: chain=SOLANA, program_id detection
(``6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P``) — either from our own
deployer_events factory column, DexScreener ``dexId == pumpfun``, or GMGN
``launchpad == pump``.

Bundle risk: enriched best-effort via our BundleDetector when holder data
is available (GMGN holders). Never blocks the feed on enrichment failure.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Query

from app.core.logging import get_logger

logger = get_logger("launches")

router = APIRouter(prefix="/launches", tags=["launches-v1"])

# pump.fun program ID (Solana) — canonical per launchpad_resolver registry
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# DexScreener / GMGN timeouts
_HTTP_TIMEOUT = 10.0


# ═══════════════════════════════════════════════════════════════════════
# Tier 1 — own data: ClickHouse deployer_events
# ═══════════════════════════════════════════════════════════════════════


def _fetch_recent_deployer_events(
    chain: str = "",
    limit: int = 50,
    pump_only: bool = False,
) -> list[dict[str, Any]]:
    """Query recent token deployments from our ClickHouse deployer_events.

    Sync (clickhouse_driver) — call via asyncio.to_thread.
    """
    try:
        from app.domains.databus.providers.chains.lite_chain_indexer import (
            _get_ch_client,
            _normalize_chain,
        )

        client = _get_ch_client()
        if client is None:
            return []
        ch_chain = _normalize_chain(chain) if chain else ""

        params: dict[str, Any] = {"limit": limit}
        where = ["type IN ('token', 'pool')"]
        if ch_chain:
            where.append("chain = %(chain)s")
            params["chain"] = ch_chain
        if pump_only:
            where.append("(factory = %(pump)s OR deployer = %(pump)s)")
            params["pump"] = PUMP_FUN_PROGRAM_ID

        where_clause = " AND ".join(where)
        rows = client.execute(
            f"""
            SELECT deployer, token_address, chain, block_number,
                   block_timestamp, tx_hash, type, factory
            FROM wallet_memory.deployer_events
            WHERE {where_clause}
            ORDER BY block_number DESC
            LIMIT %(limit)s
            """,  # noqa: S608 — verified bound/allowlisted SQL construction
            params,
        )
        events = []
        for r in rows:
            events.append(
                {
                    "deployer": r[0],
                    "token_address": r[1],
                    "chain": r[2],
                    "block_number": r[3],
                    "block_timestamp": r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4]),
                    "tx_hash": r[5],
                    "type": r[6],
                    "factory": r[7],
                }
            )
        return events
    except Exception as exc:
        logger.debug("deployer_events recent fetch failed: %s", exc)
        return []


def _own_data_launches(chain: str, limit: int, pump_only: bool) -> list[dict[str, Any]]:
    """Convert deployer_events rows into launch-feed items (Tier 1)."""
    rows = _fetch_recent_deployer_events(chain=chain, limit=limit, pump_only=pump_only)
    items: list[dict[str, Any]] = []
    for r in rows:
        factory = (r.get("factory") or "").lower()
        deployer = (r.get("deployer") or "").lower()
        is_pump = factory == PUMP_FUN_PROGRAM_ID.lower() or deployer == PUMP_FUN_PROGRAM_ID.lower()
        if pump_only and not is_pump:
            continue
        items.append(
            {
                "token_address": r.get("token_address", ""),
                "chain": r.get("chain", chain),
                "name": "",
                "symbol": "",
                "launch_timestamp": r.get("block_timestamp"),
                "liquidity": None,
                "holders": None,
                "bundle_risk": None,
                "is_pump_fun": is_pump,
                "factory": r.get("factory"),
                "deployer": r.get("deployer"),
                "tx_hash": r.get("tx_hash"),
                "source": "own_clickhouse",
            }
        )
    return items


# ═══════════════════════════════════════════════════════════════════════
# Tier 2 — DexScreener new pairs
# ═══════════════════════════════════════════════════════════════════════


async def _dexscreener_new_pairs(chain: str, limit: int, pump_only: bool) -> list[dict[str, Any]]:
    """DexScreener token-boosts + search fallback for new pairs."""
    items: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            # 1. token-boosts/latest — recent promoted launches
            resp = await c.get("https://api.dexscreener.com/token-boosts/latest/v1")
            if resp.status_code == 200 and isinstance(resp.json(), list):
                for b in resp.json()[:limit]:
                    addr = b.get("tokenAddress", "")
                    cid = b.get("chainId", "solana")
                    if chain and cid.lower() != chain.lower():
                        continue
                    items.append(
                        {
                            "token_address": addr,
                            "chain": cid,
                            "name": "",
                            "symbol": "",
                            "launch_timestamp": None,
                            "liquidity": None,
                            "holders": None,
                            "bundle_risk": None,
                            "is_pump_fun": None,
                            "source": "dexscreener_boost",
                        }
                    )

            # 2. search fallback — pump.fun tokens on solana
            if not items and (not chain or chain.lower() in ("solana", "sol")):
                q = "pump" if pump_only else "SOL"
                sresp = await c.get(
                    "https://api.dexscreener.com/latest/dex/search",
                    params={"q": q, "limit": limit},
                )
                if sresp.status_code == 200:
                    for p in (sresp.json().get("pairs") or [])[:limit]:
                        cid = p.get("chainId", "solana")
                        if chain and cid.lower() != chain.lower():
                            continue
                        dex = p.get("dexId", "")
                        is_pump = dex == "pumpfun" or "pump" in (p.get("pairAddress", "") or "").lower()
                        if pump_only and not is_pump:
                            continue
                        bt = p.get("baseToken", {}) or {}
                        created = p.get("pairCreatedAt")
                        ts = None
                        if created:
                            with contextlib.suppress(Exception):
                                ts = datetime.fromtimestamp(created / 1000, tz=UTC).isoformat()
                        items.append(
                            {
                                "token_address": bt.get("address", ""),
                                "chain": cid,
                                "name": bt.get("name", ""),
                                "symbol": bt.get("symbol", ""),
                                "launch_timestamp": ts,
                                "liquidity": (p.get("liquidity") or {}).get("usd"),
                                "holders": None,
                                "bundle_risk": None,
                                "is_pump_fun": is_pump,
                                "source": "dexscreener_search",
                            }
                        )
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError) as exc:
        logger.debug("dexscreener new pairs failed: %s", exc)
    return items[:limit]


# ═══════════════════════════════════════════════════════════════════════
# Tier 3 — GMGN launch data
# ═══════════════════════════════════════════════════════════════════════


async def _gmgn_launches(chain: str, limit: int, pump_only: bool) -> list[dict[str, Any]]:
    """GMGN pump.fun new_creation rank + bonded pairs."""
    from app.domains.databus.providers.chains.gmgn import (
        _gmgn_bonded_pairs,
        _gmgn_new_pairs,
    )

    items: list[dict[str, Any]] = []
    try:
        if not pump_only:
            res = await _gmgn_new_pairs(chain=chain, limit=limit)
        else:
            # pump.fun filter is inherent to these endpoints
            res = await _gmgn_new_pairs(chain=chain, limit=limit)
        if res and res.get("data", {}).get("launches"):
            for t in res["data"]["launches"]:
                items.append(
                    {
                        "token_address": t.get("address", ""),
                        "chain": t.get("chain", chain),
                        "name": t.get("name", ""),
                        "symbol": t.get("symbol", ""),
                        "launch_timestamp": _ts_to_iso(t.get("creation_timestamp")),
                        "liquidity": t.get("liquidity"),
                        "holders": t.get("holder_count"),
                        "bundle_risk": None,
                        "is_pump_fun": t.get("launchpad", "pump") == "pump" or True,
                        "source": "gmgn",
                    }
                )

        # bonded pairs (completed bonding curves) — append if room
        if len(items) < limit:
            bres = await _gmgn_bonded_pairs(chain=chain, limit=limit - len(items))
            if bres and bres.get("data", {}).get("launches"):
                for t in bres["data"]["launches"]:
                    items.append(
                        {
                            "token_address": t.get("address", ""),
                            "chain": t.get("chain", chain),
                            "name": t.get("name", ""),
                            "symbol": t.get("symbol", ""),
                            "launch_timestamp": _ts_to_iso(t.get("open_timestamp") or t.get("creation_timestamp")),
                            "liquidity": t.get("liquidity"),
                            "holders": None,
                            "bundle_risk": None,
                            "is_pump_fun": True,
                            "source": "gmgn_bonded",
                        }
                    )
    except Exception as exc:
        logger.debug("gmgn launches failed: %s", exc)
    return items[:limit]


def _ts_to_iso(ts: Any) -> str | None:
    """Convert epoch ms/s timestamp to ISO string, tolerant of None/0."""
    if ts is None:
        return None
    try:
        ts = int(ts)
        if ts > 10_000_000_000:  # milliseconds
            ts = ts / 1000
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


# ═══════════════════════════════════════════════════════════════════════
# Bundle risk enrichment (best-effort, non-blocking)
# ═══════════════════════════════════════════════════════════════════════


async def _enrich_bundle_risk(item: dict[str, Any]) -> dict[str, Any]:
    """Run our BundleDetector on a launch item using GMGN holder data."""
    addr = item.get("token_address", "")
    chain = item.get("chain", "solana")
    if not addr:
        return item
    try:
        from app.bundle_detector import get_bundle_detector
        from app.domains.databus.providers.chains.gmgn import _gmgn_token_holders

        holders_res = await _gmgn_token_holders(mint=addr, chain=chain, limit=100)
        holders = (holders_res or {}).get("holders") or []
        if not holders or len(holders) < 5:
            return item
        result = await get_bundle_detector().detect(
            token_address=addr,
            chain=chain,
            holders=holders,
        )
        item["bundle_risk"] = {
            "is_bundled": result.is_bundled,
            "confidence": result.confidence,
            "risk_label": result.risk_label,
            "top10_holder_pct": result.top10_holder_pct,
            "holder_count": result.holder_count,
        }
    except Exception as exc:
        logger.debug("bundle enrichment failed addr=%s err=%s", addr, exc)
    return item


# ═══════════════════════════════════════════════════════════════════════
# ROUTE
# ═══════════════════════════════════════════════════════════════════════


@router.get(
    "/recent",
    summary="Recent Token Launches",
    description=(
        "Newly launched tokens across all supported chains. Own ClickHouse "
        "deployer_events first, then DexScreener new pairs, then GMGN launch "
        "data. Optional pump.fun bonding-curve filter (chain=SOLANA, program_id "
        "detection). Includes bundle risk flag when holder data is available."
    ),
)
async def recent_launches(
    chain: str = Query("", description="Chain filter (solana, ethereum, base...). Empty = all chains."),
    limit: int = Query(20, ge=1, le=100, description="Max launches to return"),
    pump_only: bool = Query(False, description="Only pump.fun bonding-curve tokens (Solana)"),
    enrich_bundle: bool = Query(True, description="Best-effort bundle risk enrichment via our BundleDetector"),
) -> dict[str, Any]:
    """Return recent token launches, own data first, external fallbacks."""
    items: list[dict[str, Any]] = []

    # Tier 1 — own ClickHouse deployer_events
    own = await asyncio.to_thread(_own_data_launches, chain, limit, pump_only)
    items.extend(own)

    # Tier 2 — DexScreener (only if own data insufficient)
    if len(items) < limit:
        try:
            items.extend(await _dexscreener_new_pairs(chain, limit - len(items), pump_only))
        except Exception as exc:
            logger.debug("dexscreener tier failed: %s", exc)

    # Tier 3 — GMGN
    if len(items) < limit:
        try:
            items.extend(await _gmgn_launches(chain, limit - len(items), pump_only))
        except Exception as exc:
            logger.debug("gmgn tier failed: %s", exc)

    items = items[:limit]

    # Dedupe by token_address (external fallbacks may overlap own data)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for it in items:
        key = it.get("token_address", "")
        if key and key.lower() in seen:
            continue
        if key:
            seen.add(key.lower())
        deduped.append(it)
    items = deduped[:limit]

    # Bundle risk enrichment (best-effort)
    if enrich_bundle and items:
        enriched = await asyncio.gather(
            *[_enrich_bundle_risk(dict(it)) for it in items],
            return_exceptions=True,
        )
        items = [it if isinstance(it, dict) else orig for it, orig in zip(enriched, items, strict=False)]

    return {
        "items": items,
        "count": len(items),
        "chain": chain or "all",
        "pump_only": pump_only,
        "sources": sorted({it.get("source", "") for it in items}),
        "generated_at": datetime.now(UTC).isoformat(),
    }
