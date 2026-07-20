"""Scanner module — split from modules.py for maintainability."""

import time
from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    logger,
    GOPLUS_CHAIN_IDS,
    EXPLORER_APIS,
    BLOCKSCOUT_URLS,
    DEFILLAMA_API,
    SOLANA_RPC_URLS,
    SOLANA_RPC_TIMEOUT,
    _solana_rpc_call,
    _fetch_json,
    _fetch_with_fallback,
)



async def _check_goplus_security(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token security via GoPlus Security API (free, 21+ chains).

    Evaluates risk flags: honeypot status, ownership renounced, hidden owner,
    mintable, proxy, blacklist, buy/sell tax, and 15+ additional signals.

    Args:
        token_address: The token contract address to check
        chain: Blockchain network (solana, ethereum, bsc, etc.)

    Returns:
        Dict with risk_score, is_honeypot, is_mintable, holder_count, buy/sell tax,
        and other GoPlus fields. Returns error dict on failure.

    Data Sources:
        - GoPlus Security API (https://api.gopluslabs.io)
    """
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
            except Exception:
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
    except Exception as e:
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
        - app.smart_contract_honeypot_detector.HoneypotDetector
    """
    try:
        from app.smart_contract_honeypot_detector import HoneypotDetector

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
    except Exception as e:
        logger.warning("honeypot_check_failed", error=str(e))

    # Fallback: use DexScreener data for basic checks
    try:
        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            buys = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
            sells = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            price_change = pair.get("priceChange", {}).get("h24", 0) or 0

            is_honeypot = buys > 10 and sells == 0
            risk_score = 0
            if is_honeypot:
                risk_score += 60
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
                "source": "dexscreener_fallback",
            }
    except Exception as e:
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
    except Exception as e:
        logger.warning("spam_check_failed", error=str(e))

    return {"check": "spam", "result": "error", "error": "Spam check unavailable"}


# ════════════════════════════════════════════════════════════════════════════
# NEW CRITICAL CHECKS — Social Sentiment, Token Age, LP Concentration,
# Ownership Renounced, Volume/Liquidity Ratio
# ════════════════════════════════════════════════════════════════════════════


# ── 11. Social Sentiment Check ──────────────────────────────────────────
