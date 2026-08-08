"""Scanner module — split from modules.py for maintainability.

Own-Data Integration:
    _check_goplus_security now queries own_security_analysis FIRST which
    computes security indicators from ClickHouse evm_transfers + deployer_events.
    Falls back to GoPlus API only when own data has insufficient confidence.
    This eliminates the primary GoPlus API dependency for well-tracked tokens.

Degodding (wave 6, 2026-08-09):
    The Tier-1 own-data mapping, Tier-2 GoPlus HTTP call, and the 0-100
    risk-score accumulator live in ``_goplus_helpers.py``. This module keeps
    the public ``_check_goplus_security`` / ``_check_honeypot_is`` /
    ``_check_spam`` names (tests patch them at module level).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    GOPLUS_CHAIN_IDS,
    PREFER_OWN_DATA,
    _fetch_dexscreener,
    logger,
)
from app.domains.scanner.modules._goplus_helpers import (
    _goplus_api_scan,
    _map_own_security_data,
)


async def _check_goplus_security(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token security — own ClickHouse data first, GoPlus API fallback.

    Tier 1: own_security_analysis — computes from ClickHouse (evm_transfers, deployer_events)
    Tier 2: GoPlus Security API (free, 21+ chains) — original behavior

    Evaluates risk flags: honeypot status, ownership renounced, hidden owner,
    mintable, proxy, blacklist, buy/sell tax, and 15+ additional signals.

    Args:
        token_address: The token contract address to check
        chain: Blockchain network (solana, ethereum, bsc, etc.)

    Returns:
        Dict with risk_score, is_honeypot, is_mintable, holder_count, buy/sell tax,
        and other compatible fields. Returns error dict on failure.

    Data Sources:
        - Own ClickHouse evm_transfers + deployer_events (primary, zero cost)
        - GoPlus Security API (fallback, https://api.gopluslabs.io)
    """

    # ── Tier 1: Own ClickHouse security data (zero cost, always fresh) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_security_analysis import get_own_security

            own_data = await get_own_security(token_address, chain)
            if own_data.get("source") == "own" and own_data.get("confidence", 0) >= 0.5:
                logger.info(
                    "Using own security data for %s on %s (risk: %s, confidence: %s)",
                    token_address[:16],
                    chain,
                    own_data.get("risk_score", 0),
                    own_data.get("confidence"),
                )
                # Map own format to GoPlus-compatible output
                return _map_own_security_data(own_data)
            logger.debug(
                "Own security data insufficient for %s (confidence: %s), falling back to GoPlus",
                token_address[:16],
                own_data.get("confidence", 0),
            )
        except ImportError:
            logger.debug("own_security_analysis not available")
        except Exception as exc:
            logger.debug("own_security_analysis_failed", error=str(exc)[:60])

    # ── Tier 2: GoPlus API (original behavior) ──
    chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
    return await _goplus_api_scan(chain_id, token_address, chain)


# ── 2. Honeypot Detection (on-chain bytecode + fee analysis) ──────────


async def _check_honeypot_is(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check if token is a honeypot using on-chain bytecode + market analysis.

    Delegates to the HoneypotDetector which analyzes sell restrictions, fee
    logic, and transaction simulations to identify tokens that prevent selling.

    Args:
        token_address: The token contract address to check
        chain: Blockchain network identifier

    Returns:
        Dict with risk_score (0-100), risk_label, is_honeypot flag, and
        detailed findings including fee analysis and simulation results.

    Data Sources:
        - app.domains.detectors.smart_contract_honeypot_detector.HoneypotDetector
    """
    try:
        from app.domains.detectors.smart_contract_honeypot_detector import HoneypotDetector

        detector = HoneypotDetector()
        result = await detector.analyze_token(token_address, chain=chain)
        if result:
            return {
                "check": "honeypot",
                "result": "scanned",
                "risk_score": result.risk_score,
                "risk_label": result.risk_label,
                "is_honeypot": result.risk_score >= 40,
                "findings": [f.to_dict() for f in result.findings],
                "buy_tax_pct": result.buy_tax_pct,
                "sell_tax_pct": result.sell_tax_pct,
                "liquidity_locked_pct": result.liquidity_locked_pct,
                "is_proxy": result.token.is_proxy,
                "ownership_renounced": result.token.ownership_renounced,
                "owner_address": result.token.owner_address,
                "holder_count": result.token.holder_count,
                "warnings": result.warnings,
                "chain_data_sources": result.chain_data_sources,
                "scan_duration_ms": result.scan_duration_ms,
            }
    except ImportError:
        logger.warning("honeypot_detector_not_available")
    except httpx.HTTPError as e:
        logger.warning("honeypot_check_failed", error=str(e))

    # Fallback: use DEX data for basic checks (own ClickHouse data first, DexScreener API fallback)
    try:
        ds_data = await _fetch_dexscreener(token_address, chain)
        if ds_data:
            pair = ds_data["pairs"][0]
            buys = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
            sells = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            price_change = pair.get("priceChange", {}).get("h24", 0) or 0

            # Pair age from DexScreener `pairCreatedAt` (ms epoch). Own
            # ClickHouse data may omit it — then age is unknown and the
            # sells==0 honeypot rule is treated conservatively.
            pair_age_hours: float | None = None
            created_ms = pair.get("pairCreatedAt")
            if created_ms:
                pair_age_hours = (time.time() - float(created_ms) / 1000.0) / 3600.0

            # A brand-new pair legitimately shows buys with zero sells for
            # its first minutes, so the sells==0 honeypot rule only applies
            # to mature pairs (age > 0.5h).
            no_sell_activity = buys > 10 and sells == 0
            mature_pair = pair_age_hours is not None and pair_age_hours > 0.5

            if no_sell_activity and not mature_pair:
                # Inconclusive — fresh pair with no sells yet. Return no_data
                # so the scoring layer adds a mild liquidity caution instead
                # of a hard honeypot flag.
                caution_score = 0
                if liquidity < 1000:
                    caution_score += 20
                elif liquidity < 10000:
                    caution_score += 10
                return {
                    "check": "honeypot",
                    "result": "no_data",
                    "is_honeypot": False,
                    "risk_score": min(100, caution_score),
                    "buys_24h": buys,
                    "sells_24h": sells,
                    "liquidity_usd": liquidity,
                    "price_change_24h": price_change,
                    "pair_age_hours": pair_age_hours,
                    "source": "dexscreener_fallback",
                }

            is_honeypot = no_sell_activity and mature_pair
            risk_score = 0
            if is_honeypot:
                risk_score += 60
                # Tiny LP + zero sells is a stronger signal than deep LP.
                if liquidity < 1000:
                    risk_score += 15
                elif liquidity < 10000:
                    risk_score += 10
            if liquidity < 100:
                risk_score += 30
            elif liquidity < 1000:
                risk_score += 20
            if abs(price_change) > 90:
                risk_score += 10

            return {
                "check": "honeypot",
                "result": "scanned",
                "risk_score": min(100, risk_score),
                "is_honeypot": is_honeypot,
                "buys_24h": buys,
                "sells_24h": sells,
                "liquidity_usd": liquidity,
                "price_change_24h": price_change,
                "pair_age_hours": pair_age_hours,
                "source": "dexscreener_fallback",
            }
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.debug("honeypot_fallback_failed", error=str(e))

    return {"check": "honeypot", "result": "error", "error": "No data source available"}


# ── 3. Holder Data ────────────────────────────────────────────────────


async def _check_spam(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check if token is in known spam/sanctions databases."""
    try:
        from app.spam_registry import get_spam_registry

        registry = get_spam_registry()
        spam_check = registry.check_token(token_address, chain=chain)
        return {
            "check": "spam",
            "result": "scanned",
            "is_spam": spam_check.get("is_spam", False),
            "is_sanctioned": spam_check.get("is_sanctioned", False),
            "is_known_scam": spam_check.get("is_known_scam", False),
            "sources_checked": spam_check.get("sources_checked", []),
        }
    except ImportError:
        logger.warning("spam_registry_not_available")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.warning("spam_check_failed", error=str(e))

    return {"check": "spam", "result": "error", "error": "Spam check unavailable"}
