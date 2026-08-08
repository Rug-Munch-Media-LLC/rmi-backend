"""Scanner module — split from modules.py for maintainability.

Own-Data Integration:
    _check_goplus_security now queries own_security_analysis FIRST which
    computes security indicators from ClickHouse evm_transfers + deployer_events.
    Falls back to GoPlus API only when own data has insufficient confidence.
    This eliminates the primary GoPlus API dependency for well-tracked tokens.
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
                risk_score = own_data.get("risk_score", 0)
                logger.info(
                    "Using own security data for %s on %s (risk: %s, confidence: %s)",
                    token_address[:16],
                    chain,
                    risk_score,
                    own_data.get("confidence"),
                )
                # Map own format to GoPlus-compatible output
                return {
                    "check": "goplus",
                    "result": "scanned",
                    "risk_score": risk_score,
                    "is_honeypot": own_data.get("is_honeypot", False),
                    "is_mintable": own_data.get("is_mintable", False),
                    "is_proxy": own_data.get("is_proxy", False),
                    "is_blacklisted": False,
                    "is_whitelisted": False,
                    "can_take_back_ownership": False,
                    "hidden_owner": False,
                    "owner_address": own_data.get("owner_address", ""),
                    "buy_tax": own_data.get("buy_tax_pct", 0),
                    "sell_tax": own_data.get("sell_tax_pct", 0),
                    "holder_count": own_data.get("holder_count", 0),
                    "total_supply": own_data.get("total_supply", ""),
                    "source": "own_clickhouse",
                }
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
    url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params={"contract_addresses": token_address})
            if resp.status_code != 200:
                logger.debug("goplus_http_error", status=resp.status_code, chain=chain)
                return {"check": "goplus", "result": "error", "error": f"HTTP {resp.status_code}"}

            # Defensive JSON parsing
            try:
                data = resp.json()
            except httpx.HTTPError:
                logger.debug("goplus_json_parse_failed", chain=chain)
                return {"check": "goplus", "result": "error", "error": "Invalid JSON response"}

            if not isinstance(data, dict):
                return {"check": "goplus", "result": "error", "error": "Unexpected response format"}

            result = data.get("result")
            if not isinstance(result, dict):
                # GoPlus returned data but no result for this token
                return {
                    "check": "goplus",
                    "result": "scanned",
                    "note": "Token not indexed by GoPlus",
                    "risk_score": 0,
                    "source": "goplus",
                }

            token_data = result.get(token_address.lower())
            if not isinstance(token_data, dict):
                return {"check": "goplus", "result": "error", "error": "Token not found in GoPlus"}

            risk_score = 0
            is_honeypot = token_data.get("is_honeypot") == "1"
            if is_honeypot:
                risk_score += 50
            if token_data.get("can_take_back_ownership") == "1":
                risk_score += 20
            if token_data.get("is_open_source") == "0":
                risk_score += 15
            if token_data.get("is_blacklisted") == "1":
                risk_score += 40
            if token_data.get("is_whitelisted") == "1":
                risk_score -= 10
            buy_tax = float(token_data.get("buy_tax", "0") or 0)
            sell_tax = float(token_data.get("sell_tax", "0") or 0)
            if buy_tax > 10:
                risk_score += 15
            if sell_tax > 10:
                risk_score += 15
            if buy_tax > 50 or sell_tax > 50:
                risk_score += 20
            if token_data.get("is_proxy") == "1":
                risk_score += 10
            if token_data.get("is_mintable") == "1":
                risk_score += 10
            if token_data.get("owner_change_balance") == "1":
                risk_score += 15
            if token_data.get("hidden_owner") == "1":
                risk_score += 15
            if token_data.get("selfdestruct") == "1":
                risk_score += 30
            if token_data.get("transfer_pausable") == "1":
                risk_score += 10
            if token_data.get("trading_cooldown") == "1":
                risk_score += 5

            return {
                "check": "goplus",
                "result": "scanned",
                "risk_score": min(100, max(0, risk_score)),
                "is_honeypot": is_honeypot,
                "is_open_source": token_data.get("is_open_source") == "1",
                "is_proxy": token_data.get("is_proxy") == "1",
                "is_mintable": token_data.get("is_mintable") == "1",
                "is_blacklisted": token_data.get("is_blacklisted") == "1",
                "is_whitelisted": token_data.get("is_whitelisted") == "1",
                "can_take_back_ownership": token_data.get("can_take_back_ownership") == "1",
                "hidden_owner": token_data.get("hidden_owner") == "1",
                "owner_address": token_data.get("owner_address", ""),
                "buy_tax": buy_tax,
                "sell_tax": sell_tax,
                "holder_count": int(token_data.get("holder_count", "0") or 0),
                "total_supply": token_data.get("total_supply", ""),
                "source": "goplus",
            }
    except httpx.TimeoutException:
        logger.debug("goplus_timeout", chain=chain)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.warning("goplus_failed", token=token_address, error=str(e)[:100])
    return {"check": "goplus", "result": "error", "error": "GoPlus API unavailable"}


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
