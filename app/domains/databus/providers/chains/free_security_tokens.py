"""
Free token-security provider tiers (chain: ``token_security_free``).

Split out of ``free_security.py`` (degodding phase 3, 2026-08-21).
Each provider returns ``dict | None`` and follows the DataBus provider
contract: return ``None`` to fall through to the next tier in the chain.

Tier order (registered in ``registry/categories/security.py``):
  1. ``_free_security_clickhouse``   — own ClickHouse analysis (LOCAL, zero cost)
  2. ``_free_security_goplus``       — GoPlus API (free, no key, 10 req/s)
  3. ``_free_security_honeypot_is``  — Honeypot.is (free, no key, EVM only)
  4. ``_free_security_rugcheck``     — RugCheck.xyz (free, no key, Solana only)
  5. ``_free_security_dexscreener``  — DexScreener pair heuristics (free, no key)
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.databus.providers.chains.free_security_common import (
    EVM_CHAIN_IDS,
    _normalize_chain,
)

try:  # pragma: no cover - exercised only when the helper module is absent
    from app.domains.scanner.modules._goplus_helpers import _goplus_owner_renounced
except ImportError:  # pragma: no cover

    def _goplus_owner_renounced(token_data: dict[str, Any]) -> bool | None:
        owner = str(token_data.get("owner_address", "") or "").strip().lower()
        if not owner or owner in ("null", "0x0000000000000000000000000000000000000000"):
            return True
        return None


logger = get_logger("databus.providers.free_security")


async def _free_security_clickhouse(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 1 — own ClickHouse security analysis (LOCAL, zero cost).

    Computes security indicators from ClickHouse evm_transfers +
    deployer_events via the scanner's own_security_analysis engine.
    Only returns when the engine produced a confident 'own' result.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    if not token_address or not chain:
        return None
    try:
        from app.domains.scanner.engines.own_security_analysis import get_own_security

        own_data = await get_own_security(token_address, chain)
        if own_data.get("source") == "own" and own_data.get("confidence", 0) >= 0.5:
            return {
                "check": "goplus",
                "result": "scanned",
                "risk_score": own_data.get("risk_score", 0),
                "is_honeypot": own_data.get("is_honeypot", False),
                "is_mintable": own_data.get("is_mintable", False),
                "is_proxy": own_data.get("is_proxy", False),
                "owner_address": own_data.get("owner_address", ""),
                "buy_tax": own_data.get("buy_tax_pct", 0),
                "sell_tax": own_data.get("sell_tax_pct", 0),
                "holder_count": own_data.get("holder_count", 0),
                "total_supply": own_data.get("total_supply", ""),
                "source": "own_clickhouse",
                "tier": "local",
                "is_local": True,
            }
        logger.debug(
            "own security insufficient for %s (confidence: %s)",
            token_address[:16],
            own_data.get("confidence", 0),
        )
    except ImportError:
        logger.debug("own_security_analysis not available")
    except Exception as exc:  # provider contract: never raise
        logger.debug("own_security_analysis failed: %s", str(exc)[:80])
    return None


async def _free_security_goplus(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 2 — GoPlus Security API (free, no key, 10 req/s).

    https://api.gopluslabs.io/api/v1/token_security/{chain_id}
    Returns a normalized GoPlus-compatible dict or None on failure.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    if not token_address or not chain:
        return None

    chain_id = str(EVM_CHAIN_IDS.get(chain, chain))
    url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params={"contract_addresses": token_address})
            if resp.status_code != 200:
                logger.debug("goplus_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            result = data.get("result") if isinstance(data, dict) else None
            if not isinstance(result, dict):
                return None
            token_data = result.get(token_address)
            if not isinstance(token_data, dict):
                return None

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
                "owner_change_balance": token_data.get("owner_change_balance") == "1",
                "selfdestruct": token_data.get("selfdestruct") == "1",
                "transfer_pausable": token_data.get("transfer_pausable") == "1",
                "trading_cooldown": token_data.get("trading_cooldown") == "1",
                "slippage_modifiable": token_data.get("slippage_modifiable") == "1",
                "owner_renounced": _goplus_owner_renounced(token_data),
                "owner_address": token_data.get("owner_address", ""),
                "buy_tax": buy_tax,
                "sell_tax": sell_tax,
                "holder_count": int(token_data.get("holder_count", "0") or 0),
                "total_supply": token_data.get("total_supply", ""),
                "source": "goplus",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("goplus_api_failed", chain=chain)
    return None


async def _free_security_honeypot_is(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 3 — Honeypot.is API (free, no key).

    https://api.honeypot.is/v2/IsHoneypot?address=...&chainID=...
    EVM chains only. Returns honeypot status + simulated buy/sell tax.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    chain_id = EVM_CHAIN_IDS.get(chain)
    if not token_address or chain_id is None:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.honeypot.is/v2/IsHoneypot",
                params={"address": token_address, "chainID": chain_id},
            )
            if resp.status_code != 200:
                logger.debug("honeypot_is_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None

            hp_result = data.get("honeypotResult") or {}
            is_honeypot = bool(hp_result.get("isHoneypot", False))
            sim = data.get("simulationResult") or {}
            summary = data.get("summary") or {}
            token_info = data.get("token") or {}

            risk_score = 0
            if is_honeypot:
                risk_score += 60
            buy_tax = float(sim.get("buyTax", 0) or 0)
            sell_tax = float(sim.get("sellTax", 0) or 0)
            if buy_tax > 10:
                risk_score += 15
            if sell_tax > 10:
                risk_score += 15
            if buy_tax > 50 or sell_tax > 50:
                risk_score += 20
            risk_label = summary.get("risk", "unknown")

            return {
                "check": "honeypot",
                "result": "scanned",
                "risk_score": min(100, risk_score),
                "is_honeypot": is_honeypot,
                "risk_label": risk_label,
                "buy_tax": buy_tax,
                "sell_tax": sell_tax,
                "holder_count": int(token_info.get("totalHolders", 0) or 0),
                "token_name": token_info.get("name", ""),
                "token_symbol": token_info.get("symbol", ""),
                "simulation_success": data.get("simulationSuccess", False),
                "flags": hp_result.get("flags", []) or [],
                "source": "honeypot.is",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("honeypot_is_api_failed", chain=chain)
    return None


async def _free_security_rugcheck(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 4 — RugCheck.xyz (Solana only, free, no key).

    https://api.rugcheck.xyz/v1/tokens/{mint}/report
    Returns risk report with score + risk items. Non-Solana → None.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", "")))
    chain = _normalize_chain(kwargs.get("chain", ""))
    if not token_address or chain not in ("solana", ""):
        # RugCheck only indexes Solana mints
        return None
    # NOTE: Solana base58 addresses are case-sensitive — do NOT lowercase.
    token_address = str(token_address).strip()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report")
            if resp.status_code != 200:
                logger.debug("rugcheck_http_error", status=resp.status_code)
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None

            risks = data.get("risks") or []
            risk_level = 0
            risk_items = []
            for item in risks:
                if not isinstance(item, dict):
                    continue
                level = item.get("level", "")
                risk_items.append(
                    {
                        "name": item.get("name", ""),
                        "level": level,
                        "description": item.get("description", ""),
                        "score": item.get("score", 0),
                    }
                )
                if level in ("danger", "critical", "high"):
                    risk_level = max(risk_level, 3)
                elif level in ("warn", "medium"):
                    risk_level = max(risk_level, 2)
                elif level == "info":
                    risk_level = max(risk_level, 1)

            # score is 1-1000 (higher = riskier); score_normalised is 0-1
            score = int(data.get("score", 0) or 0)
            score_norm = float(data.get("score_normalised", 0) or 0)
            if risk_level == 0 and score_norm > 0.6:
                risk_level = 3

            token_meta = data.get("tokenMeta") or {}
            token_info = data.get("token") or {}
            holders = data.get("topHolders") or []

            return {
                "check": "rugcheck",
                "result": "scanned",
                "risk_score": min(100, int(score_norm * 100)),
                "rugcheck_score": score,
                "rugcheck_score_normalised": score_norm,
                "is_honeypot": risk_level >= 3,
                "risk_level": risk_level,
                "risks": risk_items,
                "token_name": token_meta.get("name", ""),
                "token_symbol": token_meta.get("symbol", ""),
                "mint_authority": token_info.get("mintAuthority"),
                "freeze_authority": token_info.get("freezeAuthority"),
                "creator": data.get("creator", ""),
                "creator_balance": data.get("creatorBalance", 0),
                "top_holder_count": len(holders) if holders else 0,
                "source": "rugcheck.xyz",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("rugcheck_api_failed", chain=chain)
    return None


async def _free_security_dexscreener(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 5 — DexScreener pair analysis heuristics (free, no key, 300 req/min).

    Last-resort safety heuristics from on-chain pair data: buy/sell asymmetry,
    liquidity depth, and extreme price moves. Returns None when no pairs exist.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    if not token_address:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            if resp.status_code != 200:
                logger.debug("dexscreener_free_security_http_error", status=resp.status_code)
                return None
            data = resp.json()
            pairs = data.get("pairs") if isinstance(data, dict) else None
            if not pairs:
                return None

            # Aggregate across all pairs
            total_buys = 0
            total_sells = 0
            total_liquidity = 0.0
            max_price_change = 0.0
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                txns = pair.get("txns") or {}
                h24 = txns.get("h24") or {}
                total_buys += int(h24.get("buys", 0) or 0)
                total_sells += int(h24.get("sells", 0) or 0)
                total_liquidity += float((pair.get("liquidity") or {}).get("usd", 0) or 0)
                pc = float((pair.get("priceChange") or {}).get("h24", 0) or 0)
                max_price_change = max(max_price_change, abs(pc))

            is_honeypot = total_buys > 10 and total_sells == 0
            risk_score = 0
            if is_honeypot:
                risk_score += 60
            if total_liquidity < 100:
                risk_score += 30
            elif total_liquidity < 1000:
                risk_score += 20
            if max_price_change > 90:
                risk_score += 10

            return {
                "check": "dexscreener",
                "result": "scanned",
                "risk_score": min(100, risk_score),
                "is_honeypot": is_honeypot,
                "buys_24h": total_buys,
                "sells_24h": total_sells,
                "liquidity_usd": total_liquidity,
                "price_change_24h_max": max_price_change,
                "pair_count": len(pairs),
                "source": "dexscreener_heuristics",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("dexscreener_free_security_failed")
    return None
