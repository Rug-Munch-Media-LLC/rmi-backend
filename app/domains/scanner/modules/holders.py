"""Scanner module — split from modules.py for maintainability.

Own-Data Integration:
    _get_holder_data now queries own_holder_analysis FIRST which computes
    holder distribution from ClickHouse evm_transfers (zero cost, always fresh).
    Falls back to RPC (Solana) / Blockscout (EVM) only when our data is empty.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    GOPLUS_CHAIN_IDS,
    PREFER_OWN_DATA,
    _fetch_dexscreener,
    _fetch_json,
    _solana_rpc_call,
    logger,
)


async def _get_holder_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get holder distribution data — own ClickHouse data first (zero cost).

    Tier 1: own_holder_analysis — computes from evm_transfers (ClickHouse)
    Tier 2: Solana RPC (getTokenLargestAccounts) or Blockscout (EVM)

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with holder_count, top_holders (ranked), concentration_pct,
        and source identifier.

    Data Sources:
        - Own ClickHouse evm_transfers (primary, zero cost)
        - Solana getTokenLargestAccounts RPC (Solana fallback)
        - Blockscout API (EVM fallback)
        - DexScreener API (last resort)
    """
    result: dict[str, Any] = {
        "check": "holders",
        "result": "scanned",
        "holder_count": 0,
        "top_holders": [],
        "concentration_pct": 0.0,
    }

    # ── Tier 1: Own ClickHouse data (zero cost, always fresh) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_holder_analysis import get_own_holder_analysis

            own_data = await get_own_holder_analysis(token_address, chain)
            if own_data.get("source") == "own" and own_data.get("top_holders"):
                result["holder_count"] = own_data.get("holder_count", 0)
                result["top_holders"] = own_data.get("top_holders", [])
                result["concentration_pct"] = own_data.get("concentration_pct", 0.0)
                result["top5_pct"] = own_data.get("top5_pct", 0.0)
                result["top10_pct"] = own_data.get("top10_pct", 0.0)
                result["gini_coefficient"] = own_data.get("gini_coefficient", 0.0)
                result["new_holders_24h"] = own_data.get("new_holders_24h", 0)
                result["source"] = "own_clickhouse"
                logger.info(
                    "Using own holder data for %s on %s (%d holders)",
                    token_address[:16],
                    chain,
                    result["holder_count"],
                )
                return result

            logger.debug(
                "Own holder data insufficient for %s, falling back",
                token_address[:16],
            )
        except ImportError:
            logger.debug("own_holder_analysis not available")
        except Exception as exc:
            logger.debug("own_holder_analysis_failed", error=str(exc)[:60])

    # ── Tier 2: Chain-specific RPC fallback ──
    if chain == "solana":
        # Solana: use getTokenLargestAccounts RPC (with fallback endpoints)
        try:
            data = await _solana_rpc_call("getTokenLargestAccounts", [token_address])
            if data:
                value = data.get("result", {}).get("value", [])
                total = sum(int(a.get("amount", "0")) for a in value) or 1
                top_holders = []
                for i, acct in enumerate(value[:20]):
                    pct = (int(acct.get("amount", "0")) / total) * 100
                    top_holders.append(
                        {
                            "rank": i + 1,
                            "address": acct.get("address", ""),
                            "amount": acct.get("amount", "0"),
                            "percentage": round(pct, 2),
                        }
                    )
                result["holder_count"] = len(value)
                result["top_holders"] = top_holders
                if top_holders:
                    result["concentration_pct"] = top_holders[0]["percentage"]
                result["source"] = "solana_rpc"
                return result
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("solana_holders_failed", error=str(e))
    else:
        # EVM: try DexScreener for holder estimate + explorer for deployer
        try:
            ds_data = await _fetch_dexscreener(token_address, chain)
            if ds_data:
                pair = ds_data["pairs"][0]
                result["holder_count"] = int(pair.get("holders", 0) or 0)
                result["source"] = "dexscreener"

            # Also try blockscout for EVM holder data
            chain_id = GOPLUS_CHAIN_IDS.get(chain, "1")
            if chain_id.isdigit():
                bs_data = await _fetch_json(
                    f"https://eth.blockscout.com/api/v2/tokens/{token_address}/holders",
                    params={"limit": 10},
                )
                if bs_data and "items" in bs_data:
                    items = bs_data["items"][:20]
                    top_holders = []
                    for i, h in enumerate(items):
                        pct = float(h.get("percentage", 0))
                        top_holders.append(
                            {
                                "rank": i + 1,
                                "address": h.get("address", {}).get("hash", ""),
                                "percentage": pct,
                            }
                        )
                    result["holder_count"] = bs_data.get("total", {}).get("value", result["holder_count"])
                    result["top_holders"] = top_holders
                    result["source"] = "blockscout"
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("evm_holders_failed", error=str(e))

    return result


# ── 4. Deployer Information ───────────────────────────────────────────


async def _check_liquidity_lock_multi(token_address: str, chain: str, pair_address: str = "") -> dict[str, Any] | None:
    """Check liquidity lock status — own ClickHouse data first, then DexScreener.

    Tier 1: own_lp_lock — analyzes LP token holders from evm_transfers (ClickHouse)
    Tier 2: HoneypotDetector — comprehensive liquidity analysis
    Tier 3: DexScreener direct lookup (last resort)

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier
        pair_address: Optional specific DEX pair address

    Returns:
        Dict with liquidity_locked_pct, liquidity_usd, lp_burned flag,
        pool_age_hours, lock_contract details, and source identifier.

    Data Sources:
        - Own ClickHouse evm_transfers + deployer_events (primary, zero cost)
        - HoneypotDetector (fallback)
        - DexScreener API (last resort)
    """
    result: dict[str, Any] = {
        "check": "liquidity",
        "result": "scanned",
        "liquidity_locked_pct": 0.0,
        "liquidity_usd": 0.0,
        "lp_burned": False,
        "pool_age_hours": 0,
    }

    # ── Tier 1: Own ClickHouse LP lock analysis (zero cost) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_lp_lock import get_own_lp_lock_status

            own_lock = await get_own_lp_lock_status(token_address, chain, pair_address)
            if own_lock.get("source") == "own" and own_lock.get("locked_pct", 0) > 0:
                result["liquidity_locked_pct"] = own_lock.get("locked_pct", 0)
                result["lp_burned"] = own_lock.get("is_burned", False)
                result["lock_contract"] = own_lock.get("lock_contract", "")
                result["lock_contract_name"] = own_lock.get("lock_contract_name", "")
                result["lp_supply"] = own_lock.get("lp_supply", "0")
                result["source"] = "own_clickhouse"

                # Also get liquidity USD from own DEX data
                try:
                    ds_data = await _fetch_dexscreener(token_address, chain)
                    if ds_data:
                        pair = ds_data["pairs"][0]
                        result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                    pass

                from app.domains.scanner.metrics import record_check_source

                record_check_source("own_data", "lp_lock")
                logger.info(
                    "Using own LP lock data for %s on %s (locked: %s%%)",
                    token_address[:16],
                    chain,
                    result["liquidity_locked_pct"],
                )
                return result

            logger.debug(
                "Own LP lock data insufficient for %s, falling back",
                token_address[:16],
            )
        except ImportError:
            logger.debug("own_lp_lock not available")
        except Exception as exc:
            logger.debug("own_lp_lock_failed", error=str(exc)[:60])

    # ── Tier 2: HoneypotDetector ──
    try:
        from app.domains.detectors.smart_contract_honeypot_detector import HoneypotDetector

        detector = HoneypotDetector()
        hp_result = await detector.analyze_token(token_address, chain=chain)
        if hp_result:
            result["liquidity_locked_pct"] = hp_result.liquidity_locked_pct
            result["liquidity_usd"] = hp_result.token.liquidity_usd
            result["lp_burned"] = hp_result.liquidity_locked_pct >= 95
            result["warnings"] = hp_result.warnings
            result["source"] = "honeypot_detector"
            return result
    except ImportError:
        pass
    except (KeyError, TypeError) as e:
        logger.debug("honeypot_liquidity_failed", error=str(e))

    # ── Tier 3: DEX data fallback (own ClickHouse first, DexScreener API fallback) ──
    try:
        ds_data = await _fetch_dexscreener(token_address, chain)
        if ds_data:
            pair = ds_data["pairs"][0]
            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            result["liquidity_usd"] = liquidity
            result["dex"] = pair.get("dexId", "")
            result["pair_address"] = pair.get("pairAddress", "")

            # Pool age
            pair_created = pair.get("pairCreatedAt", 0)
            if pair_created:
                age_hours = (time.time() - pair_created / 1000) / 3600
                result["pool_age_hours"] = round(age_hours, 1)

            # Check for LP burn on Solana
            if chain == "solana":
                pair_addr = pair.get("pairAddress", "")
                if pair_addr:
                    supply_data = await _solana_rpc_call("getTokenSupply", [pair_addr])
                    if supply_data:
                        supply = int(supply_data.get("result", {}).get("value", {}).get("amount", "0"))
                        dead_data = await _solana_rpc_call(
                            "getTokenAccountsByOwner",
                            [
                                "dead111111111111111111111111111111111111111",
                                {"mint": pair_addr},
                                {"encoding": "jsonParsed"},
                            ],
                        )
                        if dead_data:
                            dead_balance = 0
                            for acct in dead_data.get("result", {}).get("value", []):
                                dead_balance += int(
                                    acct.get("account", {})
                                    .get("data", {})
                                    .get("parsed", {})
                                    .get("info", {})
                                    .get("tokenAmount", {})
                                    .get("amount", "0")
                                )
                            if supply > 0:
                                result["liquidity_locked_pct"] = round((dead_balance / supply) * 100, 1)
                                result["lp_burned"] = dead_balance > 0

            if liquidity < 100:
                result["liquidity_locked_pct"] = 0
            result["source"] = "dexscreener"
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("liquidity_fallback_failed", error=str(e))

    return result


# ── 6. Copycat Detection ──────────────────────────────────────────────


async def _check_lp_concentration(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check LP concentration — what % of LP is held by top wallets.

    High concentration (> 50% in top 10) indicates price manipulation risk.
    Extreme concentration (> 90% in top 5) is a rug-pull flag.
    """
    result: dict[str, Any] = {
        "check": "lp_concentration",
        "result": "scanned",
        "top1_pct": 0.0,
        "top5_pct": 0.0,
        "top10_pct": 0.0,
        "holder_count": 0,
        "risk_level": "unknown",
        "is_concentrated": False,
        "risk_score": 0,
    }

    # Get holder data (reuses existing _get_holder_data)
    try:
        holder_data = await _get_holder_data(token_address, chain)
        if not holder_data or holder_data.get("result") != "scanned":
            result["result"] = "unavailable"
            return result

        top_holders = holder_data.get("top_holders", [])
        if not top_holders:
            result["result"] = "unavailable"
            return result

        result["holder_count"] = holder_data.get("holder_count", 0) or len(top_holders)
        result["source"] = holder_data.get("source", "holder_module")

        # Calculate concentration percentages
        if top_holders:
            result["top1_pct"] = top_holders[0].get("percentage", 0)

        top5_pct = sum(h.get("percentage", 0) for h in top_holders[:5] if h.get("percentage"))
        result["top5_pct"] = round(top5_pct, 2)

        top10_pct = sum(h.get("percentage", 0) for h in top_holders[:10] if h.get("percentage"))
        result["top10_pct"] = round(top10_pct, 2)

        # Risk classification
        if result["top10_pct"] >= 90:
            result["risk_level"] = "critical"
            result["is_concentrated"] = True
            result["risk_score"] = 40
        elif result["top10_pct"] >= 70:
            result["risk_level"] = "high"
            result["is_concentrated"] = True
            result["risk_score"] = 25
        elif result["top5_pct"] >= 50:
            result["risk_level"] = "medium"
            result["is_concentrated"] = True
            result["risk_score"] = 15
        else:
            result["risk_level"] = "low"
            result["risk_score"] = 0

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("lp_concentration_failed", error=str(e)[:80])
        result["result"] = "error"
        result["error"] = str(e)[:60]

    return result
