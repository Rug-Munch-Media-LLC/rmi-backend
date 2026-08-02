"""
Free API fallback providers — token security, wallet labels, market prices.

Replaces the lost Arkham API access with a stack of verified free APIs.
Each provider returns ``dict | None`` and follows the DataBus provider
contract: return ``None`` to fall through to the next tier in the chain.

Chains built from these providers (registered in registry/categories/):
  - token_security_free  (security.py) — ClickHouse → GoPlus → Honeypot.is → RugCheck → DexScreener
  - wallet_labels_free   (wallet.py)   — ClickHouse → Blockscout → Etherscan → DexScreener → heuristics
  - market_price_free    (market.py)   — ClickHouse TWAP → DexScreener → CoinGecko → GeckoTerminal → quotes

Verification (2026-08-02): all "free, no key" endpoints below were tested
live and return HTTP 200 without an API key. The only exceptions:
  - 1inch now requires an API key (HTTP 401) → registered as FREEMIUM
  - Etherscan migrated to V2 (HTTP 401 without key) → registered as FREEMIUM
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.free_security")

# ── Chain / network normalization tables ─────────────────────────────────
# EVM numeric chain IDs (GoPlus, Honeypot.is, Etherscan V2 use these)
EVM_CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "eth": 1,
    "mainnet": 1,
    "bsc": 56,
    "bnb": 56,
    "binance": 56,
    "polygon": 137,
    "matic": 137,
    "arbitrum": 42161,
    "arb": 42161,
    "optimism": 10,
    "op": 10,
    "base": 8453,
    "avalanche": 43114,
    "avax": 43114,
    "fantom": 250,
    "ftm": 250,
    "linea": 59144,
    "zksync": 324,
    "gnosis": 100,
    "xdai": 100,
    "scroll": 534352,
    "mantle": 5000,
}

# GeckoTerminal network slugs
GECKOTERMINAL_NETWORKS: dict[str, str] = {
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "bnb": "bsc",
    "polygon": "polygon_pos",
    "matic": "polygon_pos",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avax",
    "avax": "avax",
    "fantom": "fantom",
    "ftm": "fantom",
    "linea": "linea",
    "zksync": "zksync",
    "gnosis": "gnosis",
    "scroll": "scroll",
    "mantle": "mantle",
    "solana": "solana",
}

# CoinGecko platform IDs (simple/token_price/{platform_id})
COINGECKO_PLATFORMS: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "bsc": "binance-smart-chain",
    "bnb": "binance-smart-chain",
    "polygon": "polygon-pos",
    "matic": "polygon-pos",
    "arbitrum": "arbitrum-one",
    "arb": "arbitrum-one",
    "optimism": "optimistic-ethereum",
    "op": "optimistic-ethereum",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "linea": "linea",
    "zksync": "zksync-era",
    "gnosis": "xdai",
    "scroll": "scroll",
    "mantle": "mantle",
}

# Blockscout v2 API base URLs per chain
BLOCKSCOUT_URLS: dict[str, str] = {
    "ethereum": "https://eth.blockscout.com",
    "eth": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "arbitrum": "https://arbitrum.blockscout.com",
    "arb": "https://arbitrum.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
    "matic": "https://polygon.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "op": "https://optimism.blockscout.com",
    "linea": "https://linea.blockscout.com",
    "zksync": "https://zksync.blockscout.com",
    "bsc": "https://bsc.blockscout.com",
    "bnb": "https://bsc.blockscout.com",
    "avalanche": "https://avalanche.blockscout.com",
    "avax": "https://avalanche.blockscout.com",
}

# Etherscan-family V2 chain IDs
ETHERSCAN_V2_CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "eth": 1,
    "bsc": 56,
    "bnb": 56,
    "polygon": 137,
    "matic": 137,
    "arbitrum": 42161,
    "arb": 42161,
    "optimism": 10,
    "op": 10,
    "base": 8453,
    "avalanche": 43114,
    "avax": 43114,
    "fantom": 250,
    "ftm": 250,
    "linea": 59144,
    "zksync": 324,
}


def _normalize_chain(chain: str) -> str:
    """Lowercase chain name; returns '' if unknown/empty."""
    return (chain or "").strip().lower()


# ═══════════════════════════════════════════════════════════════════════════
# TOKEN SECURITY TIERS  (chain: token_security_free)
# ═══════════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════════
# WALLET / LABEL TIERS  (chain: wallet_labels_free)
# ═══════════════════════════════════════════════════════════════════════════


async def _free_blockscout_labels(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 2 — Blockscout address tags (free, no auth).

    https://{chain}.blockscout.com/api/v2/addresses/{address}
    Returns contract name, tags, ENS, and scam flag when available.
    """
    address = kwargs.get("address", kwargs.get("wallet", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    base_url = BLOCKSCOUT_URLS.get(chain)
    if not address or not base_url:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base_url}/api/v2/addresses/{address}")
            if resp.status_code != 200:
                logger.debug("blockscout_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None

            metadata = data.get("metadata") or {}
            public_tags = [t for t in (data.get("public_tags") or []) if isinstance(t, dict)]
            private_tags = [t for t in (data.get("private_tags") or []) if isinstance(t, dict)]
            tags = [t.get("display_name") or t.get("name") or "" for t in public_tags + private_tags]
            tags = [t for t in tags if t]

            label = data.get("name") or metadata.get("label") or (tags[0] if tags else "")

            result: dict[str, Any] = {
                "labels": [
                    {
                        "label": label,
                        "category": "blockscout_tag",
                        "source": "blockscout",
                        "confidence": 0.8 if label else 0.3,
                    }
                ]
                if label
                else [],
                "address": address,
                "chain": chain,
                "is_contract": data.get("is_contract", False),
                "is_verified": data.get("is_verified", False),
                "is_scam": data.get("is_scam", False),
                "ens_domain_name": data.get("ens_domain_name"),
                "tags": tags,
                "name": data.get("name", ""),
                "source": "blockscout",
                "tier": "free_api",
            }
            if not label and not tags and not data.get("is_contract"):
                # Nothing meaningful to return → fall through
                logger.debug("blockscout no tags for %s", address[:12])
                return None
            return result
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("blockscout_api_failed", chain=chain)
    return None


async def _free_etherscan_labels(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 3 — Etherscan-family labels via V2 API (free tier with key).

    https://api.etherscan.io/v2/api?chainid=1&module=contract&action=getsourcecode
    Returns the verified contract name as a label. Requires ETHERSCAN_API_KEY.
    """
    address = kwargs.get("address", kwargs.get("wallet", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    chain_id = ETHERSCAN_V2_CHAIN_IDS.get(chain)
    api_key = kwargs.get("api_key", "")
    if not address or chain_id is None or not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.etherscan.io/v2/api",
                params={
                    "chainid": chain_id,
                    "module": "contract",
                    "action": "getsourcecode",
                    "address": address,
                    "apikey": api_key,
                },
            )
            if resp.status_code != 200:
                logger.debug("etherscan_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            result = data.get("result") if isinstance(data, dict) else None
            if not isinstance(result, list) or not result:
                return None
            contract = result[0]
            if not isinstance(contract, dict):
                return None

            contract_name = (contract.get("ContractName") or "").strip()
            if not contract_name:
                return None

            return {
                "labels": [
                    {
                        "label": contract_name,
                        "category": "contract_name",
                        "source": "etherscan",
                        "confidence": 0.9,
                    }
                ],
                "address": address,
                "chain": chain,
                "contract_name": contract_name,
                "is_verified": bool(contract.get("ContractName")),
                "source": "etherscan",
                "tier": "freemium",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("etherscan_labels_failed", chain=chain)
    return None


async def _free_wallet_pattern_heuristics(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 5 — pattern-based heuristics (whale, bot, DEX router, CEX).

    Uses the local known_entities module (EXCHANGE_WALLETS, EVM_KNOWN_CONTRACTS,
    KNOWN_MARKET_MAKERS) plus transfer-volume heuristics to label an address
    when no external label source has an entry. LOCAL tier, zero cost.
    """
    address = kwargs.get("address", kwargs.get("wallet", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", "ethereum") or "ethereum")
    if not address:
        return None

    labels: list[dict[str, Any]] = []
    try:
        from app.domains.scanner.engines.known_entities import (
            get_exchange_name,
            is_bridge,
            is_cex_wallet,
            is_dex_contract,
            is_lending_protocol,
            is_market_maker,
            is_mixer,
            label_wallet,
        )

        # 1) Known-entity catalog
        known = label_wallet(address, chain)
        if known:
            entity_label = known.get("label") or known.get("name")
            if entity_label:
                labels.append(
                    {
                        "label": entity_label,
                        "category": known.get("category", "known_entity"),
                        "source": "known_entities",
                        "confidence": 0.95,
                    }
                )

        # 2) Role heuristics
        if is_cex_wallet(address, chain):
            ex_name = get_exchange_name(address, chain)
            labels.append(
                {
                    "label": f"CEX: {ex_name}" if ex_name else "CEX wallet",
                    "category": "exchange",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
        if is_dex_contract(address, chain):
            labels.append(
                {
                    "label": "DEX router/contract",
                    "category": "dex",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
        if is_market_maker(address, chain):
            labels.append(
                {
                    "label": "Market maker",
                    "category": "market_maker",
                    "source": "pattern_heuristic",
                    "confidence": 0.85,
                }
            )
        if is_bridge(address, chain):
            labels.append(
                {
                    "label": "Bridge contract",
                    "category": "bridge",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
        if is_lending_protocol(address, chain):
            labels.append(
                {
                    "label": "Lending protocol",
                    "category": "lending",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
        if is_mixer(address, chain):
            labels.append(
                {
                    "label": "Mixer",
                    "category": "mixer",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
    except ImportError:
        logger.debug("known_entities not available")
    except Exception as exc:
        logger.debug("pattern_heuristics failed: %s", str(exc)[:80])

    if not labels:
        return None

    return {
        "labels": labels,
        "address": address,
        "chain": chain,
        "source": "pattern_heuristics",
        "tier": "local",
        "is_local": True,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MARKET / PRICE TIERS  (chain: market_price_free)
# ═══════════════════════════════════════════════════════════════════════════


async def _free_market_clickhouse_twap(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 1 — own ClickHouse DEX events TWAP (LOCAL, zero cost).

    Wraps the scanner's own_price_oracle which computes TWAP from
    chain-indexer dex_events. Only returns when the oracle answered
    from own data (source == 'own').
    """
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    if not token_address or not chain:
        return None
    try:
        from app.domains.scanner.engines.own_price_oracle import get_token_price

        result = await get_token_price(token_address, chain)
        if result and result.get("source") == "own":
            result["tier"] = "local"
            result["is_local"] = True
            result["provider_source"] = "clickhouse_twap"
            return result
        logger.debug(
            "own TWAP insufficient for %s (source: %s)",
            token_address[:16],
            result.get("source") if result else "unavailable",
        )
    except ImportError:
        logger.debug("own_price_oracle not available")
    except Exception as exc:
        logger.debug("own_price_oracle failed: %s", str(exc)[:80])
    return None


async def _free_market_coingecko(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 3 — CoinGecko simple/token_price (free, 10-30 req/min, no key)."""
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    platform = COINGECKO_PLATFORMS.get(chain)
    if not token_address or not platform:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.coingecko.com/api/v3/simple/token_price/{platform}",
                params={
                    "contract_addresses": token_address,
                    "vs_currencies": "usd",
                    "include_market_cap": "true",
                    "include_24hr_vol": "true",
                    "include_24hr_change": "true",
                },
            )
            if resp.status_code != 200:
                logger.debug("coingecko_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            token_data = data.get(token_address)
            if not isinstance(token_data, dict) or "usd" not in token_data:
                return None

            return {
                "price_usd": float(token_data.get("usd", 0) or 0),
                "market_cap_usd": token_data.get("usd_market_cap"),
                "volume_24h_usd": token_data.get("usd_24h_vol"),
                "price_change_24h_pct": token_data.get("usd_24h_change"),
                "source": "coingecko",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("coingecko_free_failed", chain=chain)
    return None


async def _free_market_geckoterminal(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 4 — GeckoTerminal token price (free, no auth).

    https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{address}
    """
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", "")))
    chain = _normalize_chain(kwargs.get("chain", ""))
    network = GECKOTERMINAL_NETWORKS.get(chain)
    if not token_address or not network:
        return None
    # Solana base58 addresses are case-sensitive — preserve as-is for solana.
    token_address = str(token_address).strip() if chain == "solana" else str(token_address).lower()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{token_address}",
            )
            if resp.status_code != 200:
                logger.debug("geckoterminal_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            token_data = (data.get("data") or {}).get("attributes") if isinstance(data, dict) else None
            if not isinstance(token_data, dict) or not token_data.get("price_usd"):
                return None

            return {
                "price_usd": float(token_data.get("price_usd", 0) or 0),
                "fdv_usd": token_data.get("fdv_usd"),
                "total_supply": token_data.get("total_supply"),
                "token_name": token_data.get("name", ""),
                "token_symbol": token_data.get("symbol", ""),
                "source": "geckoterminal",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("geckoterminal_failed", chain=chain)
    return None


async def _free_market_paraswap_quote(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 5a — ParaSwap price quote (free, no auth).

    https://apiv5.paraswap.io/prices (verified HTTP 200 without key).
    Returns the destAmount implied by the best route for 1 src unit.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    network_id = EVM_CHAIN_IDS.get(chain)
    if not token_address or network_id is None:
        return None
    src_token = kwargs.get("src_token", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")  # native ETH
    src_decimals = int(kwargs.get("src_decimals", 18))
    amount = kwargs.get("amount", 10**src_decimals)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://apiv5.paraswap.io/prices",
                params={
                    "srcToken": src_token,
                    "destToken": token_address,
                    "amount": amount,
                    "srcDecimals": src_decimals,
                    "destDecimals": int(kwargs.get("dest_decimals", 18)),
                    "side": "SELL",
                    "network": network_id,
                },
            )
            if resp.status_code != 200:
                logger.debug("paraswap_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            price_route = data.get("priceRoute") if isinstance(data, dict) else None
            if not isinstance(price_route, dict):
                return None

            src_amount = int(price_route.get("srcAmount", 0) or 0)
            dest_amount = int(price_route.get("destAmount", 0) or 0)
            if src_amount <= 0 or dest_amount <= 0:
                return None

            price = dest_amount / src_amount
            return {
                "price_usd": price,
                "src_amount": src_amount,
                "dest_amount": dest_amount,
                "src_token": price_route.get("srcToken", src_token),
                "dest_token": price_route.get("destToken", token_address),
                "best_route_exchanges": [
                    swap.get("swapExchanges", [{}])[0].get("exchange", "")
                    for swap in price_route.get("bestRoute", [])
                    if isinstance(swap, dict) and swap.get("swapExchanges")
                ],
                "source": "paraswap",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("paraswap_failed", chain=chain)
    return None


async def _free_market_oneinch_quote(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 5b — 1inch quote (FREEMIUM — now requires API key, verified 401 without).

    https://api.1inch.dev/swap/v6.0/{chain_id}/quote
    """
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    chain_id = EVM_CHAIN_IDS.get(chain)
    api_key = kwargs.get("api_key", "")
    if not token_address or chain_id is None or not api_key:
        return None
    src_token = kwargs.get("src_token", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")
    amount = kwargs.get("amount", 10**18)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.1inch.dev/swap/v6.0/{chain_id}/quote",
                params={
                    "src": src_token,
                    "dst": token_address,
                    "amount": amount,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                logger.debug("oneinch_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            if not isinstance(data, dict) or "toAmount" not in data:
                return None

            to_amount = int(data.get("toAmount", 0) or 0)
            if to_amount <= 0:
                return None
            price = to_amount / amount
            return {
                "price_usd": price,
                "src_amount": amount,
                "dest_amount": to_amount,
                "src_token": src_token,
                "dest_token": token_address,
                "source": "1inch",
                "tier": "freemium",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("oneinch_failed", chain=chain)
    return None
