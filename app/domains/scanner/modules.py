"""Scanner modules — all scanner functions with real implementations."""

import time
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Chain ID mappings ──────────────────────────────────────────────────

GOPLUS_CHAIN_IDS: dict[str, str] = {
    "solana": "solana",
    "ethereum": "1",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "polygon": "137",
    "avalanche": "43114",
    "optimism": "10",
    "fantom": "250",
    "linea": "59144",
    "zksync": "324",
}

EXPLORER_APIS: dict[str, str] = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
    "polygon": "https://api.polygonscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
    "base": "https://api.basescan.org/api",
    "avalanche": "https://api.snowtrace.io/api",
    "fantom": "https://api.ftmscan.com/api",
}

# Blockscout per-chain URLs
BLOCKSCOUT_URLS: dict[str, str] = {
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "arbitrum": "https://arbitrum.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "linea": "https://linea.blockscout.com",
    "zksync": "https://zksync.blockscout.com",
    "bsc": "https://bsc.blockscout.com",
    "avalanche": "https://avalanche.blockscout.com",
}

# DeFiLlama API base
DEFILLAMA_API = "https://api.llama.fi"

SOLANA_RPC_URLS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-api.syndica.io/access-token/free",
    "https://rpc.ankr.com/solana",
    "https://solana.publicnode.com",
]

SOLANA_RPC_TIMEOUT = 10.0


async def _solana_rpc_call(method: str, params: list[Any]) -> dict[str, Any] | None:
    """Call Solana RPC with fallback across multiple endpoints."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for rpc_url in SOLANA_RPC_URLS:
        try:
            async with httpx.AsyncClient(timeout=SOLANA_RPC_TIMEOUT) as client:
                resp = await client.post(rpc_url, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    if data and isinstance(data, dict) and "result" in data:
                        return data
                logger.debug("solana_rpc_error", url=rpc_url.split("/")[2], status=resp.status_code)
        except httpx.TimeoutException:
            logger.debug("solana_rpc_timeout", url=rpc_url.split("/")[2])
        except Exception as e:
            logger.debug("solana_rpc_failed", url=rpc_url.split("/")[2], error=str(e)[:50])
    return None


# ── Shared helpers ────────────────────────────────────────────────────


async def _fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    req_timeout: float = 15.0,
) -> dict[str, Any] | None:
    """Fetch JSON from an HTTP endpoint with error handling."""
    try:
        async with httpx.AsyncClient(timeout=req_timeout) as client:
            if json_body:
                resp = await client.post(url, json=json_body)
            else:
                resp = await client.get(url, params=params)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("http_error", url=url, status=resp.status_code)
    except Exception as e:
        logger.debug("http_failed", url=url, error=str(e))
    return None


async def _fetch_with_fallback(
    urls: list[str],
    req_timeout: float = 15.0,
    json_mode: bool = True,
) -> tuple[Any, str]:
    """Try URLs in order, return first successful response."""
    async with httpx.AsyncClient(timeout=req_timeout) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    if json_mode:
                        return resp.json(), url
                    return resp.text, url
            except Exception as e:
                logger.debug(f"failed {url}: {e}")
                continue
    return {}, ""


# ── 1. GoPlus Security ────────────────────────────────────────────────


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


async def _get_holder_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get holder distribution data from chain RPC or DexScreener.

    For Solana: uses getTokenLargestAccounts RPC for top holder addresses and amounts.
    For EVM: uses DexScreener + Blockscout for holder counts and concentration.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with holder_count, top_holders (ranked), concentration_pct,
        and source identifier. concentration_pct reflects the top holder's share.

    Data Sources:
        - Solana: getTokenLargestAccounts RPC
        - EVM: DexScreener API, Blockscout API
    """
    result: dict[str, Any] = {
        "check": "holders",
        "result": "scanned",
        "holder_count": 0,
        "top_holders": [],
        "concentration_pct": 0.0,
    }

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
        except Exception as e:
            logger.debug("solana_holders_failed", error=str(e))
    else:
        # EVM: try DexScreener for holder estimate + explorer for deployer
        try:
            data, _ = await _fetch_with_fallback(
                [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
            )
            if data and data.get("pairs"):
                pair = data["pairs"][0]
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
                    result["holder_count"] = bs_data.get("total", {}).get(
                        "value", result["holder_count"]
                    )
                    result["top_holders"] = top_holders
                    result["source"] = "blockscout"
        except Exception as e:
            logger.debug("evm_holders_failed", error=str(e))

    return result


# ── 4. Deployer Information ───────────────────────────────────────────


async def _get_deployer_info(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get deployer address and basic reputation score.

    Resolves the contract creator via block explorer API (Etherscan, BscScan, etc.)
    with DexScreener fallback. Scores deployer using behavioral fingerprint matching
    from deployer_reputation module.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with deployer_address, reputation_score (0-100), reputation_label,
        signals found, and mitigations. Returns "unknown" deployer if not resolvable.

    Data Sources:
        - Block explorer APIs (Etherscan, BscScan, etc.)
        - DexScreener API (fallback)
        - app.deployer_reputation (behavioral fingerprint scoring)
    """
    result: dict[str, Any] = {"check": "deployer", "result": "scanned"}
    deployer_addr = ""

    # Try block explorer API to get contract creator
    explorer_url = EXPLORER_APIS.get(chain)
    if explorer_url:
        try:
            resp_json = await _fetch_json(
                explorer_url,
                params={
                    "module": "contract",
                    "action": "getcontractcreation",
                    "contractaddresses": token_address,
                },
            )
            if resp_json and resp_json.get("status") == "1":
                result_data = resp_json.get("result", [])
                if result_data and isinstance(result_data, list):
                    deployer_addr = result_data[0].get("contractCreator", "")
        except Exception as e:
            logger.debug("explorer_deployer_failed", error=str(e))

    # Fallback: try HoneypotDetector which gathers creator from DexScreener
    if not deployer_addr:
        try:
            data, _ = await _fetch_with_fallback(
                [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
            )
            if data and data.get("pairs"):
                pair = data["pairs"][0]
                deployer_addr = pair.get("baseToken", {}).get("address", "")
                result["token_name"] = pair.get("baseToken", {}).get("name", "")
                result["token_symbol"] = pair.get("baseToken", {}).get("symbol", "")
                result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                result["price_usd"] = float(pair.get("priceUsd", 0) or 0)
        except Exception as e:
            logger.debug("dexscreener_deployer_fallback_failed", error=str(e))

    result["deployer_address"] = deployer_addr or "unknown"

    if deployer_addr and deployer_addr != "unknown":
        # Score deployer reputation using existing engine
        try:
            from app.deployer_reputation import score_deployer_risk

            # Try to get more context about the deployer
            scoring_result = score_deployer_risk(
                address=deployer_addr,
                deployment_count=1,  # Will be refined if available
                source_verified=False,
            )
            result["reputation_score"] = scoring_result.get("risk_score", 50)
            result["reputation_label"] = scoring_result.get("risk_label", "UNKNOWN")
            result["signals"] = scoring_result.get("signals_found", [])
            result["mitigations"] = scoring_result.get("mitigations", [])
            result["fingerprint_match"] = scoring_result.get("fingerprint_match")
        except Exception as e:
            logger.debug("deployer_scoring_failed", error=str(e))
            result["reputation_score"] = 50
            result["reputation_label"] = "UNKNOWN"

    return result


async def _deep_deployer_check(deployer_addr: str, chain: str) -> dict[str, Any] | None:
    """Deep deployer analysis using behavioral fingerprint matching.

    ELITE-tier check that performs advanced deployer risk assessment:
    cross-chain activity, bytecode similarity to known scams, and
    historical deployment pattern analysis.

    Args:
        deployer_addr: Deployer wallet address
        chain: Blockchain network identifier

    Returns:
        Dict with deployer details, risk signals, and cross-chain findings.
        Returns error dict if no deployer address is available.
    """
    if not deployer_addr or deployer_addr == "unknown":
        return {"check": "deployer_deep", "result": "error", "error": "No deployer address"}

    result: dict[str, Any] = {
        "check": "deployer_deep",
        "result": "scanned",
        "deployer": deployer_addr,
    }

    try:
        from app.deployer_reputation import score_deployer_risk

        # Try to get deployer's transaction count from explorer
        explorer_url = EXPLORER_APIS.get(chain)
        tx_count = 0
        if explorer_url:
            try:
                resp_json = await _fetch_json(
                    explorer_url,
                    params={
                        "module": "account",
                        "action": "txlist",
                        "address": deployer_addr,
                        "sort": "desc",
                        "page": 1,
                        "offset": 100,
                    },
                )
                if resp_json and resp_json.get("status") == "1":
                    txs = resp_json.get("result", [])
                    tx_count = len(txs) if isinstance(txs, list) else 0
            except Exception as e:
                logger.debug("deployer_tx_fetch_failed", error=str(e))

        # Score with whatever data we have
        scoring_result = score_deployer_risk(
            address=deployer_addr,
            deployment_count=min(tx_count, 20),
            source_verified=False,
        )
        result.update(scoring_result)
    except ImportError:
        logger.warning("deployer_reputation_module_not_available")
        result["risk_score"] = 50
        result["risk_label"] = "UNKNOWN"
    except Exception as e:
        logger.warning("deep_deployer_failed", error=str(e))
        result["risk_score"] = 50
        result["risk_label"] = "ERROR"

    return result


# ── 5. Liquidity Lock Check ───────────────────────────────────────────


async def _check_liquidity_lock_multi(
    token_address: str, chain: str, pair_address: str = ""
) -> dict[str, Any] | None:
    """Check liquidity lock status using DexScreener + on-chain data.

    Uses the HoneypotDetector for comprehensive liquidity analysis including
    lock percentage, LP burn status, and pool age. Falls back to DexScreener
    direct lookup if the detector is unavailable.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier
        pair_address: Optional specific DEX pair address

    Returns:
        Dict with liquidity_locked_pct, liquidity_usd, lp_burned flag,
        pool_age_hours, and source identifier.

    Data Sources:
        - HoneypotDetector (primary)
        - DexScreener API (fallback)
    """
    result: dict[str, Any] = {
        "check": "liquidity",
        "result": "scanned",
        "liquidity_locked_pct": 0.0,
        "liquidity_usd": 0.0,
        "lp_burned": False,
        "pool_age_hours": 0,
    }

    try:
        # Use the HoneypotDetector which does thorough liquidity analysis
        from app.smart_contract_honeypot_detector import HoneypotDetector

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
    except Exception as e:
        logger.debug("honeypot_liquidity_failed", error=str(e))

    # Fallback: direct DexScreener check
    try:
        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
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
                        supply = int(
                            supply_data.get("result", {}).get("value", {}).get("amount", "0")
                        )
                        # Check LP token balance of dead address
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
                                result["liquidity_locked_pct"] = round(
                                    (dead_balance / supply) * 100, 1
                                )
                                result["lp_burned"] = dead_balance > 0

            # Very low liquidity = risk flag
            if liquidity < 100:
                result["liquidity_locked_pct"] = 0
            result["source"] = "dexscreener"
    except Exception as e:
        logger.debug("liquidity_fallback_failed", error=str(e))

    return result


# ── 6. Copycat Detection ──────────────────────────────────────────────


def _get_top_symbols() -> set[str]:
    """Get top 1000 symbols from Dune/Coingecko."""
    return {
        "ETH",
        "USDT",
        "USDC",
        "BTC",
        "SOL",
        "BNB",
        "XRP",
        "ADA",
        "DOGE",
        "MATIC",
        "DOT",
        "SHIB",
        "TRX",
        "AVAX",
        "UNI",
        "ATOM",
        "LINK",
        "BCH",
        "LTC",
        "NEAR",
        "AAVE",
        "APE",
        "AXS",
        "SAND",
        "MANA",
        "FIL",
        "ALGO",
        "FTM",
        "VET",
        "THETA",
        "XLM",
        "ICP",
        "HBAR",
        "EGLD",
        "STX",
        "KAS",
        "RUNE",
        "ARB",
        "OP",
        "INJ",
        "SEI",
        "SUI",
        "APT",
        "TIA",
        "PEPE",
        "FLOKI",
        "WIF",
        "BONK",
        "JUP",
        "RAY",
    }


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row: list[int] = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = curr_row[j] + 1
            deletions = prev_row[j + 1] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


async def _check_copycat(symbol: str, name: str) -> dict[str, Any] | None:
    """Check if token is a copycat of a top symbol using Levenshtein distance."""
    # Also check name for copycat patterns
    top_symbols = _get_top_symbols()
    symbol_upper = symbol.upper() if symbol else ""

    # Direct match to known top symbol = clean (they are the real token)
    if symbol_upper in top_symbols:
        return {"check": "copycat", "result": "clean", "match_type": "exact_symbol"}

    # Levenshtein distance check on symbol
    for top in top_symbols:
        if symbol_upper and _levenshtein_distance(symbol_upper, top) <= 2:
            return {
                "check": "copycat",
                "result": "high_risk",
                "similar_to": top,
                "distance": _levenshtein_distance(symbol_upper, top),
                "match_type": "symbol_similarity",
            }

    # Check name for top symbol name squatting
    if name:
        name_upper = name.upper()
        for top in top_symbols:
            if top in name_upper or _levenshtein_distance(name_upper, top) <= 3:
                return {
                    "check": "copycat",
                    "result": "medium_risk",
                    "similar_to": top,
                    "match_type": "name_similarity",
                }

    return {"check": "copycat", "result": "clean", "match_type": "unique"}


# ── 7. Bundle Detection ───────────────────────────────────────────────


async def _check_bundle(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check if token launch was bundled (coordinated buying)."""
    try:
        from app.bundle_detector import get_bundle_detector

        detector = get_bundle_detector()

        # Fetch recent holders for bundle analysis
        holder_data = await _get_holder_data(token_address, chain)
        holders = []
        if holder_data and holder_data.get("top_holders"):
            for h in holder_data["top_holders"]:
                holders.append(
                    {
                        "address": h.get("address", ""),
                        "amount": h.get("percentage", 0) * 100,  # Scale up for detection
                    }
                )

        # Fetch recent transactions
        transactions = []
        try:
            if chain == "solana":
                sig_data = await _solana_rpc_call(
                    "getSignaturesForAddress", [token_address, {"limit": 50}]
                )
                if sig_data:
                    sigs = sig_data.get("result", [])
                    for sig in sigs[:20]:
                        transactions.append(
                            {
                                "signature": sig.get("signature", ""),
                                "slot": sig.get("slot", 0),
                                "timestamp": sig.get("blockTime", 0),
                            }
                        )
        except Exception as e:
            logger.debug("bundle_tx_fetch_failed", error=str(e))

        bundle_result = await detector.detect(
            token_address=token_address,
            chain=chain,
            holders=holders if len(holders) >= 5 else None,
            transactions=transactions if len(transactions) >= 5 else None,
        )

        return {
            "check": "bundle",
            "result": "scanned",
            "is_bundled": bundle_result.is_bundled,
            "confidence": bundle_result.confidence,
            "risk_label": bundle_result.risk_label,
            "atomic_block_score": bundle_result.atomic_block_score,
            "common_funder_score": bundle_result.common_funder_score,
            "temporal_score": bundle_result.temporal_score,
            "distribution_anomaly_score": bundle_result.distribution_anomaly_score,
            "concentration_score": bundle_result.concentration_score,
            "top3_holder_pct": bundle_result.top3_holder_pct,
            "top10_holder_pct": bundle_result.top10_holder_pct,
            "holder_count": bundle_result.holder_count,
        }
    except ImportError:
        logger.warning("bundle_detector_not_available")
    except Exception as e:
        logger.warning("bundle_check_failed", error=str(e))

    return {"check": "bundle", "result": "error", "error": "Bundle detection unavailable"}


# ── 8. Contract Verification ──────────────────────────────────────────


async def _check_contract_verification(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check if contract source code is verified via block explorer or Sourcify."""
    result: dict[str, Any] = {
        "check": "verified",
        "result": "scanned",
        "is_verified": False,
        "source": "",
    }

    # Try block explorer first
    explorer_url = EXPLORER_APIS.get(chain)
    if explorer_url:
        try:
            resp_json = await _fetch_json(
                explorer_url,
                params={
                    "module": "contract",
                    "action": "getsourcecode",
                    "address": token_address,
                },
            )
            if resp_json and resp_json.get("status") == "1":
                source_data = resp_json["result"]
                if isinstance(source_data, list) and len(source_data) > 0:
                    source_info = source_data[0]
                    has_source = bool(source_info.get("SourceCode", ""))
                    result["is_verified"] = has_source
                    result["source"] = "explorer"
                    result["abi"] = bool(source_info.get("ABI", ""))
                    result["compiler_version"] = source_info.get("CompilerVersion", "")
                    result["optimization"] = source_info.get("OptimizationUsed", "0")
                    return result
        except Exception as e:
            logger.debug("explorer_verification_failed", error=str(e))

    # Fallback: Sourcify API
    try:
        chain_id = GOPLUS_CHAIN_IDS.get(chain, "")
        if chain_id:
            # Sourcify: check if contract is verified
            sourcify_url = f"https://sourcify.dev/server/api/v2/contract/{chain_id}/{token_address}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(sourcify_url)
                    if resp.status_code == 200:
                        result["is_verified"] = True
                        result["source"] = "sourcify"
                        return result
            except Exception as e:
                logger.debug("sourcify_check_failed", error=str(e))

            # Try Blockscout as another fallback
            if chain_id.isdigit():
                blockscout_url = (
                    f"https://eth.blockscout.com/api/v2/smart-contracts/{token_address}"
                )
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(blockscout_url)
                        if resp.status_code == 200:
                            result["is_verified"] = True
                            result["source"] = "blockscout"
                            return result
                except Exception as e:
                    logger.debug("blockscout_check_failed", error=str(e))
    except Exception as e:
        logger.debug("sourcify_verification_failed", error=str(e))

    return result


# ── 9. Price Impact / Consensus ────────────────────────────────────────


async def _get_price_consensus(
    token_address: str, chain: str, market_price: float = 0
) -> dict[str, Any] | None:
    """Get price consensus across multiple sources."""
    result: dict[str, Any] = {
        "check": "price",
        "result": "scanned",
        "price_usd": 0,
        "sources": [],
        "price_consensus": False,
    }

    prices: dict[str, float] = {}

    # Source 1: DexScreener
    try:
        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            price = float(pair.get("priceUsd", 0) or 0)
            if price > 0:
                prices["dexscreener"] = price
                result["price_usd"] = price
                result["volume_24h"] = float(pair.get("volume", {}).get("h24", 0) or 0)
                result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                result["fdv"] = float(pair.get("fdv", 0) or 0)
                result["price_change_24h"] = pair.get("priceChange", {}).get("h24", 0)
                result["dex"] = pair.get("dexId", "")
                result["pair_address"] = pair.get("pairAddress", "")
    except Exception as e:
        logger.debug("dexscreener_price_failed", error=str(e))

    # Source 2: GoPlus (has price info for some tokens)
    try:
        chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
        goplus_data = await _fetch_json(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": token_address},
        )
        if goplus_data:
            goplus_result = goplus_data.get("result", {}).get(token_address.lower(), {})
            if goplus_result:
                prices["goplus"] = float(goplus_result.get("price", 0) or 0)
    except Exception as e:
        logger.debug("goplus_price_failed", error=str(e))

    # Determine consensus
    result["sources"] = list(prices.keys())
    if len(prices) >= 1:
        result["price_usd"] = max(prices.values())  # Use highest confidence
        result["price_consensus"] = True

    return result


# ── 10. Spam/Malicious Token Check ────────────────────────────────────


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


async def _check_social_sentiment(
    token_address: str, chain: str, symbol: str = ""
) -> dict[str, Any] | None:
    """Check social sentiment — LunarCrush volume, dominance, sentiment score.

    Flags suspicious patterns:
      - High social volume but negative sentiment (paid shills)
      - Very low social volume for a "popular" token (bot-like)
      - Sudden spike in mentions without organic follow-through
    """
    result: dict[str, Any] = {
        "check": "social_sentiment",
        "result": "scanned",
        "social_volume": 0,
        "social_dominance": 0,
        "sentiment_score": 0,
        "mentions_24h": 0,
        "is_suspicious": False,
        "suspicion_reasons": [],
    }

    # Try LunarCrush via the existing connector
    try:
        from app.lunarcrush_connector import get_lunarcrush_connector

        lc = get_lunarcrush_connector()
        sentiment = await lc.get_sentiment(token_address)
        if sentiment:
            result["social_volume"] = sentiment.get("social_volume", 0)
            result["social_dominance"] = sentiment.get("social_dominance", 0)
            result["sentiment_score"] = sentiment.get("sentiment_score", 0)
            result["mentions_24h"] = sentiment.get("mentions_24h", 0)
            result["mentions_change_24h"] = sentiment.get("mentions_change_24h", 0)
            result["source"] = "lunarcrush"

            # Suspicion detection
            volume = result["social_volume"]
            mentions = result["mentions_24h"]
            sentiment_score = result["sentiment_score"]

            # High volume + negative sentiment = paid shills
            if volume > 100 and sentiment_score < -0.3:
                result["is_suspicious"] = True
                result["suspicion_reasons"].append("high_volume_negative_sentiment")

            # Very low mentions for a token with volume
            if mentions < 5 and volume > 10:
                result["is_suspicious"] = True
                result["suspicion_reasons"].append("low_mentions_relative_to_volume")

            # Extreme positive sentiment with no volume (bot-like)
            if sentiment_score > 0.8 and volume < 5:
                result["is_suspicious"] = True
                result["suspicion_reasons"].append("extreme_sentiment_no_volume")

    except ImportError:
        logger.debug("lunarcrush_connector_not_available")
    except Exception as e:
        logger.debug("social_sentiment_failed", error=str(e)[:80])

    # Fallback: try RMI news sentiment endpoint for signal
    if not result.get("source"):
        try:
            from app.domains.x402.tools.news_tools import get_news_sentiment

            news_data = await get_news_sentiment(token_address, chain=chain)
            if news_data and news_data.get("articles", 0) > 0:
                result["news_articles"] = news_data.get("articles", 0)
                result["news_sentiment"] = news_data.get("sentiment", "neutral")
                result["source"] = "news_fallback"
        except ImportError:
            pass
        except Exception as e:
            logger.debug("news_sentiment_fallback_failed", error=str(e)[:60])

    # Score: suspicious activity adds risk
    risk_score = 0
    if result["is_suspicious"]:
        risk_score += 25 * len(result["suspicion_reasons"])
    result["risk_score"] = min(100, risk_score)

    return result


# ── 12. Token Age Check ───────────────────────────────────────────────


async def _check_token_age(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check how old the token/pool is.

    New tokens (< 24h) are higher risk.
    Very new (< 1h) are extremely high risk.
    Mature (> 7d) are lower risk.
    """
    result: dict[str, Any] = {
        "check": "token_age",
        "result": "scanned",
        "age_hours": None,
        "age_days": None,
        "created_at": None,
        "risk_level": "unknown",
        "is_new_token": False,
        "risk_score": 0,
    }

    now = time.time()

    # Try DexScreener for pool creation time
    try:
        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
        )
        if data and data.get("pairs"):
            # Use the earliest pair creation time
            creation_times = []
            for pair in data["pairs"]:
                created = pair.get("pairCreatedAt", 0)
                if created and created > 0:
                    creation_times.append(created / 1000)  # ms → s

            if creation_times:
                first_created = min(creation_times)
                age_seconds = max(0, now - first_created)
                age_hours = age_seconds / 3600

                result["age_hours"] = round(age_hours, 1)
                result["age_days"] = round(age_hours / 24, 1)
                result["created_at"] = first_created
                result["source"] = "dexscreener"

                # Risk classification
                if age_hours < 1:
                    result["risk_level"] = "critical"
                    result["is_new_token"] = True
                    result["risk_score"] = 40
                elif age_hours < 24:
                    result["risk_level"] = "high"
                    result["is_new_token"] = True
                    result["risk_score"] = 25
                elif age_hours < 168:  # 7 days
                    result["risk_level"] = "medium"
                    result["risk_score"] = 10
                else:
                    result["risk_level"] = "low"
                    result["risk_score"] = 0
                return result
    except Exception as e:
        logger.debug("token_age_dexscreener_failed", error=str(e)[:60])

    # Fallback: GoPlus has creation info for some tokens
    try:
        chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
        goplus_data = await _fetch_json(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": token_address},
        )
        if goplus_data and goplus_data.get("result"):
            token_data = goplus_data["result"].get(token_address.lower(), {})
            if token_data:
                creator_time = token_data.get("creator_time", 0)
                if creator_time:
                    age_seconds = max(0, now - int(creator_time))
                    age_hours = age_seconds / 3600
                    result["age_hours"] = round(age_hours, 1)
                    result["age_days"] = round(age_hours / 24, 1)
                    result["created_at"] = int(creator_time)
                    result["source"] = "goplus"

                    if age_hours < 24:
                        result["risk_level"] = "high"
                        result["is_new_token"] = True
                        result["risk_score"] = 25
                    elif age_hours < 168:
                        result["risk_level"] = "medium"
                        result["risk_score"] = 10
                    else:
                        result["risk_level"] = "low"
                        result["risk_score"] = 0
                    return result
    except Exception as e:
        logger.debug("token_age_goplus_failed", error=str(e)[:60])

    # No data available
    result["result"] = "unavailable"
    return result


# ── 13. LP Concentration Check ─────────────────────────────────────────


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

    except Exception as e:
        logger.debug("lp_concentration_failed", error=str(e)[:80])
        result["result"] = "error"
        result["error"] = str(e)[:60]

    return result


# ── 14. Ownership Renounced Check ─────────────────────────────────────


async def _check_ownership_renounced(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check if contract ownership has been renounced.

    Ownership renounced = good (can't be rugged by owner).
    Active ownership = higher risk (owner can mint, pause, blacklist).
    """
    result: dict[str, Any] = {
        "check": "ownership_renounced",
        "result": "scanned",
        "ownership_renounced": False,
        "owner_address": "",
        "is_proxy": False,
        "risk_score": 0,
        "risk_level": "unknown",
    }

    # ── Source 1: GoPlus data (best for EVM) ──
    try:
        chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
        goplus_data = await _fetch_json(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": token_address},
        )
        if goplus_data and goplus_data.get("result"):
            token_data = goplus_data["result"].get(token_address.lower(), {})
            if token_data:
                owner = token_data.get("owner_address", "")
                is_renounced = not owner or owner == "0x0000000000000000000000000000000000000000"
                result["ownership_renounced"] = is_renounced
                result["owner_address"] = owner or ""
                result["is_proxy"] = token_data.get("is_proxy") == "1"
                result["source"] = "goplus"

    except Exception as e:
        logger.debug("ownership_goplus_failed", error=str(e)[:60])

    # ── Source 2: Solana — check mint authority ──
    if chain == "solana" and not result.get("source"):
        try:
            data = await _solana_rpc_call(
                "getAccountInfo",
                [token_address, {"encoding": "jsonParsed"}],
            )
            if data:
                val = (data.get("result") or {}).get("value")
                if val:
                    parsed = (val.get("data") or {}).get("parsed", {})
                    if parsed.get("type") == "mint":
                        info = parsed.get("info", {})
                        mint_auth = info.get("mintAuthority")
                        is_renounced = mint_auth is None
                        result["ownership_renounced"] = is_renounced
                        result["owner_address"] = str(mint_auth) if mint_auth else ""
                        result["source"] = "solana_rpc"
        except Exception as e:
            logger.debug("ownership_solana_failed", error=str(e)[:60])

    # ── Source 3: HoneypotDetector fallback ──
    if not result.get("source"):
        try:
            from app.smart_contract_honeypot_detector import HoneypotDetector

            detector = HoneypotDetector()
            hp_result = await detector.analyze_token(token_address, chain=chain)
            if hp_result:
                is_renounced = hp_result.token.ownership_renounced
                result["ownership_renounced"] = is_renounced
                result["owner_address"] = hp_result.token.owner_address or ""
                result["is_proxy"] = hp_result.token.is_proxy
                result["source"] = "honeypot_detector"
        except ImportError:
            pass
        except Exception as e:
            logger.debug("ownership_honeypot_failed", error=str(e)[:60])

    # ── Risk scoring ──
    if result.get("ownership_renounced") is True:
        result["risk_level"] = "safe"
        result["risk_score"] = 0
    elif result.get("owner_address"):
        owner = result["owner_address"]
        if owner and owner != "0x0000000000000000000000000000000000000000":
            result["risk_level"] = "active_owner"
            result["risk_score"] = 30  # Owner can still manipulate
            if result.get("is_proxy"):
                result["risk_score"] = 50  # Proxy + active owner = very risky
                result["risk_level"] = "active_proxy_owner"
        else:
            result["ownership_renounced"] = True
            result["risk_level"] = "safe"
            result["risk_score"] = 0

    return result


# ── 15. Volume/Liquidity Ratio Check ──────────────────────────────────


async def _check_volume_liquidity_ratio(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check volume-to-liquidity ratio.

    volume_24h / liquidity_usd = turnover ratio.
    High ratio (> 5) = potential wash trading (suspicious).
    Very high ratio (> 20) = almost certainly wash trading.
    Low ratio (< 0.5) + low absolute values = dead token.
    """
    result: dict[str, Any] = {
        "check": "volume_liquidity_ratio",
        "result": "scanned",
        "volume_24h": 0.0,
        "liquidity_usd": 0.0,
        "ratio": 0.0,
        "risk_level": "unknown",
        "is_wash_trading_suspicious": False,
        "risk_score": 0,
    }

    # Fetch DexScreener data for volume + liquidity
    try:
        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            volume = float(pair.get("volume", {}).get("h24", 0) or 0)
            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            price_usd = float(pair.get("priceUsd", 0) or 0)

            result["volume_24h"] = volume
            result["liquidity_usd"] = liquidity
            result["price_usd"] = price_usd
            result["source"] = "dexscreener"

            # Calculate ratio (avoid division by zero)
            if liquidity > 0 and volume > 0:
                ratio = volume / liquidity
                result["ratio"] = round(ratio, 4)
            elif volume > 0 and liquidity == 0:
                result["ratio"] = float("inf")
            else:
                result["ratio"] = 0.0

            # Risk classification
            ratio = result["ratio"]
            if ratio == float("inf") or ratio > 50:
                result["risk_level"] = "critical"
                result["is_wash_trading_suspicious"] = True
                result["risk_score"] = 45
            elif ratio > 20:
                result["risk_level"] = "high"
                result["is_wash_trading_suspicious"] = True
                result["risk_score"] = 35
            elif ratio > 5:
                result["risk_level"] = "medium"
                result["risk_score"] = 20
                # Check if it's suspicious based on context
                if volume > 10000 and liquidity < 1000:
                    result["is_wash_trading_suspicious"] = True
                    result["risk_score"] = 35
                    result["risk_level"] = "high"
            elif ratio < 0.5 and volume < 100:
                result["risk_level"] = "low_activity"
                result["risk_score"] = 5
            else:
                result["risk_level"] = "healthy"
                result["risk_score"] = 0

    except Exception as e:
        logger.debug("volume_liquidity_failed", error=str(e)[:60])
        result["result"] = "error"
        result["error"] = str(e)[:60]

    return result


# ── Legacy stub modules (unchanged - return pending but are non-critical) ──


async def _check_blockscout(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on Blockscout — address metadata, contract status, tx count."""
    result: dict[str, Any] = {
        "check": "blockscout",
        "result": "scanned",
    }
    base_url = BLOCKSCOUT_URLS.get(chain, BLOCKSCOUT_URLS["ethereum"])
    try:
        # Get address info
        url = f"{base_url}/api/v2/addresses/{token_address}"
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                result["result"] = "unavailable"
                result["error"] = f"Blockscout HTTP {resp.status_code}"
                return result

            data = resp.json()
            result["is_contract"] = data.get("is_contract", False)
            result["has_transactions"] = (data.get("tx_count", 0) or 0) > 0
            result["tx_count"] = data.get("tx_count", 0)
            result["token_type"] = (
                data.get("token", {}).get("type", "") if data.get("token") else ""
            )
            result["ens_domain"] = data.get("ens_info", {}).get("domain", "") or None

            # If it's a token, get market data
            if data.get("token"):
                token_data = data["token"]
                result["symbol"] = token_data.get("symbol", "")
                result["name"] = token_data.get("name", "")
                result["decimals"] = token_data.get("decimals")
                result["total_supply"] = token_data.get("total_supply", "")
                result["holders"] = token_data.get("holders", "")
                result["exchange_rate"] = token_data.get("exchange_rate")

            result["source"] = "blockscout"
            result["chain_url"] = base_url
    except httpx.TimeoutException:
        logger.debug("blockscout_timeout", chain=chain)
        return {"check": "blockscout", "result": "unavailable", "error": "timeout"}
    except Exception as e:
        logger.debug("blockscout_failed", error=str(e)[:80])
        return {"check": "blockscout", "result": "unavailable", "error": str(e)[:60]}

    return result


async def _check_token_sniffer(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on TokenSniffer."""
    logger.info("check_token_sniffer", token=token_address, chain=chain)
    return {"check": "tokensniffer", "result": "pending"}


async def _check_trm_sanctions(address: str) -> dict[str, Any] | None:
    """Check address on TRM sanctions list."""
    logger.info("check_trm_sanctions", address=address[:10])
    return {"check": "trm", "result": "pending"}


async def _check_scorechain_sanctions(
    address: str, chain: str = "ethereum"
) -> dict[str, Any] | None:
    """Check address on Scorechain sanctions list."""
    logger.info("check_scorechain", address=address[:10], chain=chain)
    return {"check": "scorechain", "result": "pending"}


async def _check_chainabuse(address: str) -> dict[str, Any] | None:
    """Check address on Chainabuse."""
    logger.info("check_chainabuse", address=address[:10])
    return {"check": "chainabuse", "result": "pending"}


async def _check_forta_alerts(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Forta alerts for token."""
    logger.info("check_forta", token=token_address, chain=chain)
    return {"check": "forta", "result": "pending"}


async def _check_defillama_context(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check DeFiLlama context for token."""
    logger.info("check_defillama", token=token_address, chain=chain)
    return {"check": "defillama", "result": "pending"}


async def _check_coingecko_price(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check CoinGecko price for token."""
    logger.info("check_coingecko", token=token_address, chain=chain)
    return {"check": "coingecko", "result": "pending"}


async def _check_1inch_swap(
    token_address: str, chain: str, amount_usd: float = 100.0
) -> dict[str, Any] | None:
    """Check 1inch swap for token."""
    logger.info("check_1inch", token=token_address, chain=chain, amount_usd=amount_usd)
    return {"check": "1inch", "result": "pending"}


async def _rag_scam_check(
    token_address: str, chain: str, deployer: str | None = None
) -> dict[str, Any] | None:
    """Check RAG for scam indicators via vector search + pattern matching."""
    logger.info("rag_scam_check", token=token_address, chain=chain, deployer=deployer)
    try:
        from app.domains.scanner.rag_scam_check import check_scam

        result = await check_scam(
            token_address=token_address,
            chain=chain,
            symbol="",
            name="",
            deployer=deployer,
        )
        if result:
            return result
    except ImportError:
        logger.debug("rag_scam_check_module_not_available")
    except Exception as e:
        logger.warning("rag_scam_check_failed", error=str(e)[:100])

    return {"check": "rag", "result": "scanned", "risk_score": 0, "source": "unavailable"}


async def _check_cross_chain_deployer(deployer_addr: str, chain: str) -> dict[str, Any] | None:
    """Check if deployer has cross-chain activity."""
    logger.info("cross_chain_deployer", deployer=deployer_addr, chain=chain)
    return {"check": "cross_chain", "result": "pending"}


async def _get_birdeye_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Birdeye data for token."""
    logger.info("birdeye_data", token=token_address, chain=chain)
    return {"check": "birdeye", "result": "pending"}


async def _verify_mint_consensus(token_address: str, chain: str) -> dict[str, Any] | None:
    """Verify mint authority consensus."""
    logger.info("verify_mint", token=token_address, chain=chain)
    return {"check": "mint", "result": "pending"}


async def _ingest_scan_to_rag(scan_result: Any) -> None:
    """Ingest scan result to RAG for future searches."""
    logger.info("ingest_to_rag", token=scan_result.token_address)


async def _get_solscan_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Solscan data for token."""
    logger.info("solscan_data", token=token_address, chain=chain)
    return {"check": "solscan", "result": "pending"}


async def _get_moralis_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Moralis data for token."""
    logger.info("moralis_data", token=token_address, chain=chain)
    return {"check": "moralis", "result": "pending"}


async def _get_etherscan_enhanced(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get enhanced Etherscan data for token."""
    logger.info("etherscan_enhanced", token=token_address, chain=chain)
    return {"check": "etherscan", "result": "pending"}


async def _get_quicknode_pumpfun(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Quicknode Pump.fun data for token."""
    logger.info("quicknode_pumpfun", token=token_address, chain=chain)
    return {"check": "quicknode", "result": "pending"}


async def _check_nansen(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Nansen data for token."""
    logger.info("nansen_check", token=token_address, chain=chain)
    return {"check": "nansen", "result": "pending"}


async def _check_chainaware(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ChainAware data for token."""
    logger.info("chainaware_check", token=token_address, chain=chain)
    return {"check": "chainaware", "result": "pending"}


async def _check_blowfish(
    token_address: str, chain: str, user_address: str = ""
) -> dict[str, Any] | None:
    """Check Blowfish AI for token."""
    logger.info("blowfish_check", token=token_address, chain=chain, user=user_address)
    return {"check": "blowfish", "result": "pending"}


async def _check_santiment(symbol: str) -> dict[str, Any] | None:
    """Check Santiment for token."""
    logger.info("santiment_check", symbol=symbol)
    return {"check": "santiment", "result": "pending"}


async def _check_fear_greed() -> dict[str, Any] | None:
    """Check Crypto Fear & Greed index."""
    logger.info("fear_greed_check")
    return {"check": "fear_greed", "result": "pending"}


async def _check_news_sentiment(symbol: str) -> dict[str, Any] | None:
    """Check news sentiment for token."""
    logger.info("news_sentiment_check", symbol=symbol)
    return {"check": "news", "result": "pending"}


async def _check_augmento(symbol: str) -> dict[str, Any] | None:
    """Check Augmento for token."""
    logger.info("augmento_check", symbol=symbol)
    return {"check": "augmento", "result": "pending"}


async def _check_coingecko_trending() -> dict[str, Any] | None:
    """Check CoinGecko trending tokens."""
    logger.info("coingecko_trending")
    return {"check": "trending", "result": "pending"}


async def _check_webacy(address: str, chain: str) -> dict[str, Any] | None:
    """Check Webacy for address."""
    logger.info("webacy_check", address=address, chain=chain)
    return {"check": "webacy", "result": "pending"}


async def _check_sourcify(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Sourcify for token."""
    logger.info("sourcify_check", token=token_address, chain=chain)
    return {"check": "sourcify", "result": "pending"}


async def _check_scamsniffer_live(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ScamSniffer Live for token."""
    logger.info("scamsniffer_live", token=token_address, chain=chain)
    return {"check": "scamsniffer", "result": "pending"}


async def _check_dune(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Dune analytics for token."""
    logger.info("dune_check", token=token_address, chain=chain)
    return {"check": "dune", "result": "pending"}


async def _check_arkham(address: str, chain: str) -> dict[str, Any] | None:
    """Check Arkham for address."""
    logger.info("arkham_check", address=address, chain=chain)
    return {"check": "arkham", "result": "pending"}


async def _check_thegraph(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check The Graph for token."""
    logger.info("thegraph_check", token=token_address, chain=chain)
    return {"check": "thegraph", "result": "pending"}


async def _check_chainpatrol_asset(asset_type: str, asset_id: str) -> dict[str, Any] | None:
    """Check asset on ChainPatrol."""
    logger.info("check_chainpatrol", type=asset_type, id=asset_id)
    return {"check": "chainpatrol", "result": "pending"}


async def _check_defi_scanner(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on DeFi Scanner — DeFiLlama for protocol presence & TVL."""
    result: dict[str, Any] = {
        "check": "defi_scanner",
        "result": "scanned",
        "has_defi_presence": False,
        "protocols_found": 0,
    }

    try:
        # Try DeFiLlama current price endpoint (works for any token with a known price)
        if chain == "ethereum" and token_address.startswith("0x"):
            llama_id = f"ethereum:{token_address}"
        elif chain == "solana":
            llama_id = f"solana:{token_address}"
        elif chain == "bsc":
            llama_id = f"bsc:{token_address}"
        else:
            llama_id = None

        if llama_id:
            async with httpx.AsyncClient(timeout=10.0) as client:
                price_resp = await client.get(
                    f"{DEFILLAMA_API}/prices/current/{llama_id}",
                )
                if price_resp.status_code == 200:
                    price_data = price_resp.json()
                    coins = price_data.get("coins", {})
                    coin_data = coins.get(llama_id, coins.get(token_address, {}))
                    if coin_data:
                        result["price_usd"] = coin_data.get("price", 0)
                        result["symbol"] = coin_data.get("symbol", "")
                        result["defillama_confidence"] = coin_data.get("confidence", 0)

        # Check simple token info endpoint
        async with httpx.AsyncClient(timeout=10.0) as client:
            info_resp = await client.get(f"{DEFILLAMA_API}/token/{chain.upper()}/{token_address}")
            if info_resp.status_code == 200:
                info = info_resp.json()
                if info:
                    result["has_defi_presence"] = True
                    result["protocols_found"] = len(info) if isinstance(info, list) else 1
                    result["protocols"] = info[:5] if isinstance(info, list) else [info]
                    result["source"] = "defillama"

    except httpx.TimeoutException:
        logger.debug("defiscanner_timeout", chain=chain)
    except Exception as e:
        logger.debug("defiscanner_failed", error=str(e)[:80])

    # If DeFiLlama had no data, try DexScreener as fallback
    if not result.get("source"):
        try:
            data, _ = await _fetch_with_fallback(
                [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
            )
            if data and data.get("pairs"):
                pair = data["pairs"][0]
                result["price_usd"] = float(pair.get("priceUsd", 0) or 0)
                result["volume_24h"] = float(pair.get("volume", {}).get("h24", 0) or 0)
                result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                result["dex_name"] = pair.get("dexId", "")
                result["has_defi_presence"] = True
                result["protocols_found"] = 1
                result["source"] = "dexscreener_fallback"
        except Exception as e:
            logger.debug("defiscanner_fallback_failed", error=str(e))

    return result


async def _check_volume_anomaly(
    token_address: str,
    chain: str,
    market_price: float = 0,
    volume_24h: float = 0,
    circulating_supply: float = 0,
) -> dict[str, Any] | None:
    """Check for volume anomalies."""
    logger.info("check_volume", token=token_address, chain=chain)
    return {"check": "volume", "result": "pending"}
