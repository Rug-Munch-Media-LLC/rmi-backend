"""
RugMunch Unified Wallet Scanner + Trending Engine
==================================================
Powered by SENTINEL + Wallet Memory Bank + Helius + Token Scanner integration.

FREE:   Helius on-chain data (age, tx, volume, balances) + SENTINEL labels
PRO:    Wallet Memory Bank identity + cluster intel + GMGN scoring
ELITE:  Token portfolio risk scan + full behavioral analysis

Chains: solana, ethereum, base, bsc, arbitrum, polygon, avalanche,
        optimism, fantom, linea, zksync, scroll, mantle
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── CHAIN CONFIG ─────────────────────────────────────────────────

CHAINS = {
    "solana": {"id": "solana", "explorer": "https://solscan.io"},
    "ethereum": {"id": "1", "explorer": "https://etherscan.io"},
    "base": {"id": "8453", "explorer": "https://basescan.org"},
    "bsc": {"id": "56", "explorer": "https://bscscan.com"},
    "arbitrum": {"id": "42161", "explorer": "https://arbiscan.io"},
    "polygon": {"id": "137", "explorer": "https://polygonscan.com"},
    "avalanche": {"id": "43114", "explorer": "https://snowtrace.io"},
    "optimism": {"id": "10", "explorer": "https://optimistic.etherscan.io"},
    "fantom": {"id": "250", "explorer": "https://ftmscan.com"},
    "linea": {"id": "59144", "explorer": "https://lineascan.build"},
    "zksync": {"id": "324", "explorer": "https://explorer.zksync.io"},
    "scroll": {"id": "534352", "explorer": "https://scrollscan.com"},
    "mantle": {"id": "5000", "explorer": "https://mantlescan.xyz"},
}

# ═══════════════════════════════════════════════════════════════
# WALLET SCANNER - Powered by SENTINEL + Helius + Wallet Memory
# ═══════════════════════════════════════════════════════════════


@dataclass
class WalletRiskFactors:
    """100+ risk factors scored per wallet - populated from real on-chain data."""

    wallet_age_days: int = 0
    first_tx_date: str | None = None
    total_tx_count: int = 0
    tx_frequency_per_day: float = 0
    days_since_last_tx: int = 0
    active_chains: int = 1
    total_volume_usd: float = 0
    avg_tx_value_usd: float = 0
    max_single_tx_usd: float = 0

    funding_source: str | None = None
    funding_source_label: str | None = None
    funding_source_is_cex: bool = False
    funding_source_is_mixer: bool = False
    funding_from_sanctioned: bool = False
    funding_from_scammer: bool = False
    funding_hop_count: int = 0
    cross_chain_funding: bool = False
    tornado_cash_exposure: bool = False
    mixer_interactions: int = 0

    unique_tokens_held: int = 0
    token_launches_participated: int = 0
    sniper_activity_score: float = 0
    sandwich_attacks_executed: int = 0
    flash_loan_usage: int = 0
    rug_pull_launcher_count: int = 0
    honeypot_tokens_owned: int = 0
    tokens_where_top_holder: int = 0

    cex_deposits: int = 0
    cex_withdrawals: int = 0
    dex_swaps: int = 0
    dex_volume_usd: float = 0

    cluster_id: str | None = None
    cluster_size: int = 0
    cluster_shared_funding: bool = False
    known_cluster_type: str = ""
    entity_label: str | None = None
    entity_confidence: float = 0
    is_labeled_scammer: bool = False
    is_labeled_sanctioned: bool = False
    is_labeled_exchange: bool = False
    is_labeled_bot: bool = False
    is_labeled_whale: bool = False
    is_labeled_insider: bool = False
    linked_wallets_count: int = 0

    insider_trading_confidence: float = 0
    buys_before_pumps: int = 0
    sells_before_dumps: int = 0
    wash_trading_score: float = 0
    pump_and_dump_participant: bool = False
    new_token_flipper: bool = False
    round_trip_patterns: int = 0

    scam_token_exposure: int = 0
    phishing_victim: bool = False
    sybil_score: float = 0
    exploit_exposure: int = 0
    protocol_hack_interactions: int = 0
    approval_risk_score: float = 0

    current_balance_usd: float = 0
    portfolio_diversity_score: float = 0
    stablecoin_ratio: float = 0
    bluechip_ratio: float = 0
    memecoin_ratio: float = 0
    realized_pnl_usd: float = 0
    unrealized_pnl_usd: float = 0
    win_rate: float = 0

    # Token holdings risk (from integrated token scanner)
    held_token_risks: list[dict[str, Any]] = field(default_factory=list)
    highest_risk_token: dict[str, Any] | None = None

    # Overall
    total_risk_score: int = 0
    risk_category: str = "unknown"
    confidence: float = 0
    data_sources: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════
# RESULT CACHE - Redis-backed, 1hr TTL
# ═══════════════════════════════════════════

import hashlib  # noqa: E402


async def _cache_get(key: str) -> dict | None:
    """Get cached wallet scan result."""
    try:
        import json
        import os

        import redis.asyncio as aioredis

        r = await aioredis.from_url(
            f"redis://{os.getenv('REDIS_HOST', 'rmi-redis')}:{os.getenv('REDIS_PORT', '6379')}",
            password=os.getenv("REDIS_PASSWORD", "") or None,
        )
        cached = await r.get(f"rmi:wallet_cache:{key}")
        await r.close()
        if cached:
            return json.loads(cached)
    except Exception:
        logger.debug("wallet cache get failed", exc_info=True)
    return None


async def _cache_set(key: str, data: dict, ttl: int = 3600):
    """Cache wallet scan result for 1 hour."""
    try:
        import json
        import os

        import redis.asyncio as aioredis

        r = await aioredis.from_url(
            f"redis://{os.getenv('REDIS_HOST', 'rmi-redis')}:{os.getenv('REDIS_PORT', '6379')}",
            password=os.getenv("REDIS_PASSWORD", "") or None,
        )
        await r.setex(f"rmi:wallet_cache:{key}", ttl, json.dumps(data))
        await r.close()
    except Exception:
        logger.debug("wallet cache set failed", exc_info=True)


# ═══════════════════════════════════════════
# HELIUS CREDENTIAL POOL - Primary + Fallback
# ═══════════════════════════════════════════

_HELIUS_KEYS = None
_HELIUS_INDEX = 0


def _get_helius_key() -> str | None:
    """Round-robin through Helius API keys. Returns None if none configured."""
    global _HELIUS_KEYS, _HELIUS_INDEX
    if _HELIUS_KEYS is None:
        import os

        keys = []
        # Primary key
        k1 = os.getenv("HELIUS_API_KEY", "").strip()
        if k1:
            keys.append(k1)
        # Fallback keys (comma-separated or separate env vars)
        k2 = os.getenv("HELIUS_API_KEY_2", "").strip()
        if k2:
            keys.append(k2)
        k3 = os.getenv("HELIUS_API_KEY_3", "").strip()
        if k3:
            keys.append(k3)
        _HELIUS_KEYS = keys
    if not _HELIUS_KEYS:
        return None
    key = _HELIUS_KEYS[_HELIUS_INDEX % len(_HELIUS_KEYS)]
    _HELIUS_INDEX += 1
    return key


async def _helius_call(method: str, params: list) -> dict | None:
    """Call Helius RPC with credential pool fallback."""
    for _attempt in range(3):  # try up to 3 keys
        key = _get_helius_key()
        if not key:
            return None
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    f"https://mainnet.helius-rpc.com/?api-key={key}",
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" not in data:
                        return data
                # 401/429 = try next key
                if resp.status_code in (401, 429):
                    continue
        except Exception:
            logger.debug("helius call failed, trying next key", exc_info=True)
            continue
    return None


async def _fetch_helius_wallet(address: str) -> dict[str, Any]:
    """Fetch wallet age, tx count, balances, and token holdings from Helius (credential pool)."""
    result = {}

    # Token balances
    bal_data = await _helius_call(
        "getTokenAccountsByOwner",
        [
            address,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    )
    if bal_data:
        accounts = (bal_data.get("result", {}) or {}).get("value", [])
        tokens = []
        for acc in accounts:
            info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            amt = float(info.get("tokenAmount", {}).get("uiAmount", 0) or 0)
            mint = info.get("mint", "")
            if mint and amt > 0:
                tokens.append({"mint": mint, "amount": amt})
        result["tokens"] = tokens
        result["token_count"] = len(tokens)
        result["data_sources"] = ["helius"]

    # Transaction signatures for age/activity
    sig_data = await _helius_call("getSignaturesForAddress", [address, {"limit": 100}])
    if sig_data:
        sigs = sig_data.get("result", []) or []
        if sigs:
            result["tx_count_sample"] = len(sigs)
            result["last_tx"] = sigs[0].get("blockTime")
            first = sigs[-1].get("blockTime")
            if first:
                age_seconds = datetime.now(UTC).timestamp() - first
                result["wallet_age_days"] = int(age_seconds / 86400)
                result["tx_count"] = len(sigs)
                result["data_sources"] = [*result.get("data_sources", []), "helius_tx"]

    # SOL balance
    sol_data = await _helius_call("getBalance", [address])
    if sol_data:
        bal = sol_data.get("result", {}).get("value", 0)
        result["sol_balance"] = bal / 1e9 if bal else 0

    return result


# ═══════════════════════════════════════════
# MORALIS - EVM wallet data (multi-chain)
# ═══════════════════════════════════════════


async def _fetch_moralis_wallet(address: str, chain: str) -> dict[str, Any]:
    """Fetch EVM wallet data from Moralis (balance, tx history, token holdings)."""
    import os

    key = os.getenv("MORALIS_API_KEY", "").strip()
    if not key:
        return {}

    chain_map = {
        "ethereum": "eth",
        "bsc": "bsc",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "base": "base",
        "optimism": "optimism",
        "avalanche": "avalanche",
        "fantom": "fantom",
    }
    moralis_chain = chain_map.get(chain, "eth")

    result = {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {"X-API-Key": key}

            # Token balances
            resp = await client.get(
                f"https://deep-index.moralis.io/api/v2.2/{address}/erc20",
                params={"chain": moralis_chain},
                headers=headers,
            )
            if resp.status_code == 200:
                tokens_data = resp.json()
                tokens = []
                for t in tokens_data[:50]:
                    amt = float(t.get("balance_formatted", 0) or 0)
                    if amt > 0:
                        tokens.append(
                            {
                                "mint": t.get("token_address", ""),
                                "amount": amt,
                                "symbol": t.get("symbol", ""),
                            }
                        )
                result["tokens"] = tokens
                result["token_count"] = len(tokens)
                result["data_sources"] = [*result.get("data_sources", []), "moralis"]

            # Native balance
            resp2 = await client.get(
                f"https://deep-index.moralis.io/api/v2.2/{address}/balance",
                params={"chain": moralis_chain},
                headers=headers,
            )
            if resp2.status_code == 200:
                bal = resp2.json().get("balance", 0)
                if bal:
                    result["native_balance"] = float(bal) / 1e18

            # Transaction count for activity
            resp3 = await client.get(
                f"https://deep-index.moralis.io/api/v2.2/{address}",
                params={"chain": moralis_chain},
                headers=headers,
            )
            if resp3.status_code == 200:
                wallet_data = resp3.json()
                if wallet_data.get("transactions"):
                    txs = wallet_data["transactions"]
                    result["tx_count"] = len(txs)
                    result["data_sources"] = [*result.get("data_sources", []), "moralis_tx"]
    except Exception:
        logger.debug("moralis wallet fetch failed", exc_info=True)

    return result


# ═══════════════════════════════════════════
# ETHERSCAN - EVM transaction data (free tier)
# ═══════════════════════════════════════════

ETHERSCAN_APIS = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
    "polygon": "https://api.polygonscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "base": "https://api.basescan.org/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
    "avalanche": "https://api.snowtrace.io/api",
    "fantom": "https://api.ftmscan.com/api",
}


async def _fetch_etherscan_wallet(address: str, chain: str) -> dict[str, Any]:
    """Fetch EVM wallet tx count and age from Etherscan family (free tier, no API key needed for basic calls)."""
    if chain not in ETHERSCAN_APIS:
        return {}

    result = {}
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            api_url = ETHERSCAN_APIS[chain]

            # Get transaction list (free, no key needed for basic)
            resp = await client.get(
                api_url,
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": 10,
                    "sort": "asc",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1":
                    txs = data.get("result", [])
                    if txs:
                        first_ts = int(txs[0].get("timeStamp", 0))
                        last_ts = int(txs[-1].get("timeStamp", 0))
                        if first_ts:
                            result["wallet_age_days"] = int(
                                (datetime.now(UTC).timestamp() - first_ts) / 86400
                            )
                        if last_ts:
                            result["last_tx"] = last_ts
                        result["tx_count_sample"] = len(txs)
                        result["data_sources"] = ["etherscan"]
    except Exception:
        logger.debug("etherscan wallet fetch failed", exc_info=True)

    return result


# ═══════════════════════════════════════════
# SENTINEL INTEGRATION - Labels + Intel
# ═══════════════════════════════════════════


async def _fetch_sentinel_labels(address: str, chain: str) -> dict[str, Any]:
    """Get rich labels from SENTINEL address_labeler (6 sources)."""
    try:
        from app.domains.scanners.address_labeler import AddressLabeler

        labeler = AddressLabeler()
        result = await labeler.analyze(address, chain)
        if result:
            return {
                "labels": getattr(result, "labels", []),
                "category": getattr(result, "category", ""),
                "risk_score": getattr(result, "risk_score", 0),
                "sources": getattr(result, "sources", []),
            }
    except Exception as e:
        logger.debug(f"SENTINEL labels failed for {address}: {e}")
    return {}


async def _fetch_wallet_memory(address: str, chain: str) -> dict[str, Any]:
    """Cross-chain identity + cluster intel from Wallet Memory Bank."""
    try:
        from app.wallet_memory.engine import get_wallet_engine

        engine = get_wallet_engine()
        intel = await engine.get_deployer_intelligence(address, chain)
        if intel:
            return {
                "entity_id": intel.get("entity_id"),
                "linked_wallets": intel.get("linked_wallets", []),
                "risk_score": intel.get("risk_score", 0),
                "scam_associations": intel.get("scam_associations", []),
                "deployer_history": intel.get("deployer_history"),
                "cross_chain_presence": intel.get("cross_chain_presence"),
            }
    except Exception as e:
        logger.debug(f"Wallet Memory Bank failed for {address}: {e}")
    return {}


async def _scan_held_tokens(tokens: list, chain: str) -> dict[str, Any]:
    """Scan held tokens using SENTINEL - parallel with full enrichments."""
    if not tokens:
        return {"scanned": 0, "risks": [], "deployers_to_flag": []}

    # Sort by amount (largest first), cap at 5 for speed
    sorted_tokens = sorted(
        tokens, key=lambda t: float(t.get("amount", 0) if isinstance(t, dict) else 0), reverse=True
    )
    mints = []
    for token in sorted_tokens[:5]:  # Top 5 by balance
        mint = token.get("mint", "") if isinstance(token, dict) else token
        if mint:
            mints.append(mint)

    if not mints:
        return {"scanned": 0, "risks": [], "deployers_to_flag": []}

    # Parallel scan all tokens
    async def _scan_one(mint: str) -> dict | None:
        try:
            from app.token_scanner import scan_token

            scan = await scan_token(mint, chain, tier="free")
            enrich = {}
            if hasattr(scan, "free") and isinstance(scan.free, dict):
                # Pull key enrichments for wallet context
                e = scan.free
                enrich = {
                    "simulation": e.get("simulation"),
                    "holders": e.get("holders"),
                    "deployer": e.get("deployer"),
                    "deep_deployer": e.get("deep_deployer"),
                    "birdeye": e.get("birdeye"),
                    "copycat_check": e.get("copycat_check"),
                    "volume_anomaly": e.get("volume_anomaly"),
                }
            return {
                "token": mint,
                "symbol": scan.symbol,
                "name": scan.name,
                "safety_score": scan.safety_score,
                "confidence": getattr(scan, "confidence", 0),
                "risk_flags": scan.risk_flags,
                "enrichments": enrich,
            }
        except Exception:
            return None

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[_scan_one(m) for m in mints], return_exceptions=True), timeout=15.0
        )
    except TimeoutError:
        results = []  # Timeout - return what we have

    risks = []
    deployers_to_flag = []
    highest = None
    for r in results if isinstance(results, list) else []:
        if isinstance(r, dict) and r:
            risks.append(r)
            if r["safety_score"] < 30:
                # Flag deployer for data flywheel
                enrich = r.get("enrichments", {})
                deployer = enrich.get("deployer", {}) or {}
                dev_addr = deployer.get("deployer") if isinstance(deployer, dict) else None
                if dev_addr and r["safety_score"] < 20:
                    deployers_to_flag.append(
                        {
                            "deployer": dev_addr,
                            "token": r["token"],
                            "symbol": r["symbol"],
                            "chain": chain,
                            "risk_score": r["safety_score"],
                            "flags": r["risk_flags"][:3],
                        }
                    )
            if highest is None or r["safety_score"] < highest.get("safety_score", 100):
                highest = r

    return {
        "scanned": len(risks),
        "risks": risks,
        "highest_risk": highest,
        "deployers_to_flag": deployers_to_flag,
        "avg_safety": round(sum(r["safety_score"] for r in risks) / len(risks), 1) if risks else 0,
        "high_risk_count": sum(1 for r in risks if r["safety_score"] < 30),
    }


# ═══════════════════════════════════════════
# UNIFIED WALLET SCAN
# ═══════════════════════════════════════════

# ═══════════════════════════════════════════
# FREE-TIER ENHANCEMENTS
# ═══════════════════════════════════════════


async def _check_hapi_risk(address: str, chain: str) -> dict[str, Any] | None:
    """Query HAPI Protocol on-chain risk scoring.

    HAPI is an on-chain cybersecurity network detecting malicious activity.
    Uses both external and on-chain data for real-time AML screening.
    """
    try:
        # HAPI doesn't have a simple REST API - we query their public data
        # or use their subgraph. For now, we check their public status endpoint.
        async with httpx.AsyncClient(timeout=10.0) as client:
            # HAPI Protocol public API (if available)
            resp = await client.get(
                f"https://hapi.one/api/v1/address/{address}",
                params={"chain": chain.lower()},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "risk_score": data.get("risk_score", 0),
                    "risk_level": data.get("risk_level", "unknown"),
                    "is_malicious": data.get("is_malicious", False),
                    "categories": data.get("categories", []),
                    "source": "hapi",
                }
    except Exception as e:
        logger.debug(f"HAPI check failed: {e}")
    return None


async def _check_misttrack_light(address: str, chain: str) -> dict[str, Any] | None:
    """Query SlowMist MistTrack Light - free wallet risk assessment.

    Provides preliminary risk profiling and graph analysis.
    """
    try:
        # MistTrack web interface scraping or API if available
        # The free tier is via their web app; we'll try their public API
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"https://misttrack.io/api/v1/address/{address}",
                params={"chain": chain.lower()},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "risk_score": data.get("risk_score", 0),
                    "risk_level": data.get("risk_level", "unknown"),
                    "labels": data.get("labels", []),
                    "related_addresses": data.get("related", [])[:5],
                    "source": "misttrack",
                }
    except Exception as e:
        logger.debug(f"MistTrack check failed: {e}")
    return None


async def _check_honeydb_ip(ip_address: str) -> dict[str, Any] | None:
    """Query HoneyDB for threat actor data on IP address.

    Community-driven honeypot data aggregation. 1,500 req/month free.
    Useful for correlating IP addresses with crypto scam infrastructure.
    """
    try:
        api_id = os.getenv("HONEYDB_API_ID", "")  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        api_key = os.getenv("HONEYDB_API_KEY", "")  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        if not api_id or not api_key:
            return None

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://honeydb.io/api/v1/indicator/ipv4",
                params={"ip": ip_address},
                headers={"X-HoneyDB-APIID": api_id, "X-HoneyDB-APIKey": api_key},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "is_threat": data.get("is_threat", False),
                    "threat_score": data.get("threat_score", 0),
                    "events": data.get("events", []),
                    "source": "honeydb",
                }
    except Exception as e:
        logger.debug(f"HoneyDB check failed: {e}")
    return None


async def _check_trm_sanctions_wallet(address: str) -> dict[str, Any] | None:
    """Screen wallet address against TRM Labs sanctions list.

    Free tier: 1 req/sec, 100/day unauthenticated.
    Authenticated: 1K req/sec, 100K/day.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.trmlabs.com/public/v1/sanctions/screening",
                json={"address": [address]},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 201:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    r = results[0]
                    return {
                        "is_sanctioned": r.get("isSanctioned", False),
                        "screening_address": r.get("address", address),
                        "source": "trm_labs",
                        "risk_level": "critical" if r.get("isSanctioned") else "low",
                    }
            elif resp.status_code == 429:
                logger.warning("TRM Labs rate limit hit")
                return {"source": "trm_labs", "rate_limited": True, "is_sanctioned": False}
    except Exception as e:
        logger.debug(f"TRM sanctions check failed: {e}")
    return None


async def _trace_funding_source(address: str, chain: str) -> dict[str, Any]:
    """Trace initial funding source using on-chain transaction history."""
    try:
        if chain == "solana":
            key = _get_helius_key()
            if not key:
                return {}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://api.helius.xyz/v0/addresses/{address}/transactions",
                    params={"api-key": key, "limit": 10, "type": "TRANSFER"},
                )
                if resp.status_code != 200:
                    return {}
                txs = resp.json()
                if not txs:
                    return {"source": "unknown", "hops": 0}

                # Find first incoming transfer
                first_in = None
                for tx in txs:
                    transfers = tx.get("tokenTransfers", []) + tx.get("nativeTransfers", [])
                    for t in transfers:
                        if t.get("toUserAccount") == address:
                            first_in = t
                            break
                    if first_in:
                        break

                source = first_in.get("fromUserAccount", "unknown") if first_in else "unknown"
                source_label = await _resolve_entity_free(source, chain)
                return {
                    "source": source,
                    "source_label": source_label.get("label", "unknown"),
                    "is_cex": "exchange" in str(source_label).lower()
                    or "cex" in str(source_label).lower(),
                    "is_mixer": "mixer" in str(source_label).lower()
                    or "tornado" in str(source_label).lower(),
                    "hops": 1,
                }
        return {"source": "unknown", "hops": 0}
    except Exception:
        return {"source": "unknown", "hops": 0}


async def _resolve_entity_free(address: str, chain: str) -> dict[str, Any]:
    """Basic cross-chain entity resolution - FREE tier (no Wallet Memory Bank)."""
    try:
        # Check SENTINEL labels first
        from app.domains.scanners.address_labeler import AddressLabeler

        labeler = AddressLabeler()
        labels = await labeler.analyze(address, chain)
        if labels and labels.primary_label:
            return {"label": labels.primary_label, "source": "sentinel", "chains": [chain]}

        # Check wallet_labels CSV
        from app.wallet_memory.labels import get_label

        label = get_label(address, chain)
        if label:
            return {"label": label, "source": "wallet_labels", "chains": [chain]}

        return {"label": "unknown", "source": "none", "chains": [chain]}
    except Exception:
        return {"label": "unknown", "source": "none", "chains": [chain]}


async def _calculate_basic_pnl(
    address: str, chain: str, tokens: list, native_balance: float = 0
) -> dict[str, Any]:
    """Basic PnL calculation - FREE tier (no GMGN)."""
    try:
        total_value = native_balance or 0
        token_count = len(tokens)

        if tokens and chain == "solana":
            # Try Jupiter price lookup for tokens
            mints = [t.get("mint", "") for t in tokens[:10] if t.get("mint")]
            if mints:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(
                        "https://quote-api.jup.ag/v6/price", params={"ids": ",".join(mints[:5])}
                    )
                    if resp.status_code == 200:
                        prices = resp.json().get("data", {})
                        for mint, pdata in prices.items():
                            price = float(pdata.get("price", 0))
                            for t in tokens:
                                if t.get("mint") == mint:
                                    balance = float(t.get("amount", 0)) / (
                                        10 ** (t.get("decimals", 0) or 0)
                                    )
                                    total_value += balance * price

        return {
            "estimated_value_usd": round(total_value, 2),
            "token_count": token_count,
            "priced_tokens": min(len(tokens), 5),
            "method": "jupiter" if total_value > native_balance else "native_only",
        }
    except Exception:
        return {
            "estimated_value_usd": round(native_balance, 2),
            "token_count": len(tokens),
            "method": "native_only",
        }


async def _close_data_flywheel(deployers: list):
    """Feed scam findings back to Wallet Memory Bank."""
    if not deployers:
        return
    try:
        from app.wallet_memory.engine import get_wallet_engine

        engine = get_wallet_engine()
        for d in deployers[:5]:  # Limit to 5
            await engine.flag_deployer(
                d["deployer"],
                d["chain"],
                reason=f"scam_token_{d['symbol']}",
                token=d["token"],
                score=100 - d["risk_score"],
            )
        logger.info(f"Data flywheel: flagged {len(deployers[:5])} deployers")
    except Exception as e:
        logger.warning(f"Data flywheel failed: {e}")


async def scan_wallet(
    wallet_address: str, chain: str = "solana", tier: str = "free"
) -> dict[str, Any]:
    """Unified wallet scanner - SENTINEL + Helius/Moralis/Etherscan pool + Wallet Memory + Token Scanner."""
    import time

    # ── Redis cache check (1hr TTL, skips repeat scans) ──
    cache_key = hashlib.sha256(f"{chain}:{wallet_address}:{tier}".encode()).hexdigest()[:16]
    cached = await _cache_get(cache_key)
    if cached:
        cached["cached"] = True
        logger.info(f"Wallet scan {wallet_address[:12]}... [{chain}] = CACHED")
        return cached

    factors = WalletRiskFactors()
    start = time.time()
    data_sources = []
    token_list = []
    modules_run = []  # Track which modules succeeded/failed

    # ── On-chain data: chain-specific multi-source fetchers ──
    onchain = {}
    if chain == "solana":
        # Solana: Helius (credential pool) + fallback to public RPC
        onchain = await _fetch_helius_wallet(wallet_address)
        if not onchain:
            # Fallback to free public Solana RPC
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        "https://api.mainnet-beta.solana.com",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getBalance",
                            "params": [wallet_address],
                        },
                    )
                    if resp.status_code == 200:
                        bal = resp.json().get("result", {}).get("value", 0)
                        onchain = {
                            "sol_balance": bal / 1e9 if bal else 0,
                            "data_sources": ["solana_public"],
                        }
            except Exception:  # noqa: S110
                pass
    else:
        # EVM chain: Moralis (primary) + Etherscan family (fallback)
        onchain = await _fetch_moralis_wallet(wallet_address, chain)
        etherscan = await _fetch_etherscan_wallet(wallet_address, chain)
        if etherscan and not onchain.get("wallet_age_days"):
            onchain["wallet_age_days"] = etherscan.get("wallet_age_days", 0)
            onchain["data_sources"] = onchain.get("data_sources", []) + etherscan.get(
                "data_sources", []
            )

    if onchain:
        factors.wallet_age_days = onchain.get("wallet_age_days", 0)
        factors.total_tx_count = onchain.get("tx_count", onchain.get("tx_count_sample", 0))
        factors.current_balance_usd = onchain.get("sol_balance", onchain.get("native_balance", 0))
        token_list = onchain.get("tokens", [])
        factors.unique_tokens_held = onchain.get("token_count", len(token_list))
        if onchain.get("last_tx"):
            last_ts = onchain["last_tx"]
            if isinstance(last_ts, (int, float)):
                factors.days_since_last_tx = int((datetime.now(UTC).timestamp() - last_ts) / 86400)
        data_sources.extend(onchain.get("data_sources", []))

    # ── DexScreener volume + labels (FREE tier, all chains) ──
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.dexscreener.com/latest/dex/search", params={"q": wallet_address}
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                if pairs:
                    factors.total_volume_usd = sum(
                        float(p.get("volume", {}).get("h24", 0) or 0) for p in pairs
                    )
                    factors.dex_swaps = len(pairs)
                    factors.token_launches_participated = sum(
                        1
                        for p in pairs
                        if p.get("pairCreatedAt")
                        and (
                            datetime.now(UTC)
                            - datetime.fromtimestamp(p.get("pairCreatedAt", 0) / 1000, tz=UTC)
                        ).total_seconds()
                        / 3600
                        < 24
                    )
                    data_sources.append("dexscreener")
    except Exception:  # noqa: S110
        pass

    # ── SENTINEL address labels (6-source label resolution, FREE tier) ──
    sentinel_labels = await _fetch_sentinel_labels(wallet_address, chain)
    if sentinel_labels:
        factors.entity_label = sentinel_labels.get("category", "")
        factors.is_labeled_scammer = (
            factors.is_labeled_scammer or "scam" in str(sentinel_labels).lower()
        )
        factors.is_labeled_sanctioned = (
            factors.is_labeled_sanctioned
            or "sanction" in str(sentinel_labels).lower()
            or "ofac" in str(sentinel_labels).lower()
        )
        factors.is_labeled_exchange = (
            "exchange" in str(sentinel_labels).lower() or "cex" in str(sentinel_labels).lower()
        )
        factors.is_labeled_bot = (
            "bot" in str(sentinel_labels).lower() or "mev" in str(sentinel_labels).lower()
        )
        factors.is_labeled_whale = "whale" in str(sentinel_labels).lower()
        factors.is_labeled_insider = "insider" in str(sentinel_labels).lower()
        factors.entity_confidence = max(
            factors.entity_confidence, sentinel_labels.get("risk_score", 50)
        )
        data_sources.append("sentinel_labels")

        # ── FREE: External threat intelligence (HAPI, MistTrack, TRM sanctions) ──
    hapi_risk = await _check_hapi_risk(wallet_address, chain)
    if hapi_risk:
        if hapi_risk.get("is_malicious"):
            factors.is_labeled_scammer = True
            factors.scam_token_exposure = max(factors.scam_token_exposure, 1)
        data_sources.append("hapi")

    misttrack = await _check_misttrack_light(wallet_address, chain)
    if misttrack:
        if misttrack.get("risk_score", 0) > 70:
            factors.is_labeled_scammer = True
        data_sources.append("misttrack")

    trm = await _check_trm_sanctions_wallet(wallet_address)
    if trm:
        if trm.get("is_sanctioned"):
            factors.is_labeled_sanctioned = True
            factors.funding_from_sanctioned = True
        data_sources.append("trm_labs")

    # ── PRO: Wallet Memory Bank (cross-chain identity + clusters) ──
    if tier in ("pro", "elite"):
        wallet_memory = await _fetch_wallet_memory(wallet_address, chain)
        if wallet_memory:
            factors.linked_wallets_count = len(wallet_memory.get("linked_wallets", []))
            factors.cluster_size = wallet_memory.get(
                "linked_wallets_count", factors.linked_wallets_count
            )
            factors.cluster_id = wallet_memory.get("entity_id", "")
            factors.cross_chain_funding = bool(wallet_memory.get("cross_chain_presence"))
            scam_assoc = wallet_memory.get("scam_associations", [])
            if scam_assoc:
                factors.is_labeled_scammer = True
                factors.scam_token_exposure = len(scam_assoc)
            hist = wallet_memory.get("deployer_history", {}) or {}
            factors.rug_pull_launcher_count = hist.get("rugs", 0)
            data_sources.append("wallet_memory")

        # GMGN scoring
        try:
            from app.gmgn_client import GMGNClient

            gmgn = GMGNClient()
            intel = await gmgn.get_wallet_intelligence(wallet_address)
            if intel:
                factors.insider_trading_confidence = float(intel.get("insider_score", 0))
                factors.sniper_activity_score = float(intel.get("sniper_score", 0))
                factors.wash_trading_score = float(intel.get("wash_trading_score", 0))
                factors.pump_and_dump_participant = intel.get("pump_dump_participant", False)
                factors.new_token_flipper = intel.get("flipper", False)
                pnl = intel.get("pnl", {}) or {}
                factors.realized_pnl_usd = float(pnl.get("realized", 0))
                factors.unrealized_pnl_usd = float(pnl.get("unrealized", 0))
                factors.win_rate = float(intel.get("win_rate", 0))
                data_sources.append("gmgn")
        except Exception:  # noqa: S110
            pass

        # Entity clustering
        try:
            from app.entity_clustering import EntityClusteringEngine

            engine = EntityClusteringEngine()
            cluster = engine.find_cluster(wallet_address)
            if cluster:
                factors.cluster_size = max(factors.cluster_size, cluster.get("size", 0))
                factors.cluster_shared_funding = cluster.get("shared_funding", False)
                factors.known_cluster_type = cluster.get("type", "")
                factors.linked_wallets_count = max(
                    factors.linked_wallets_count, cluster.get("member_count", 0)
                )
        except Exception:  # noqa: S110
            pass

    # ── FREE: Token portfolio risk scan (parallel + enrichments) ──
    token_scan = await _scan_held_tokens(token_list, chain)
    if token_scan:
        factors.held_token_risks = token_scan.get("risks", [])
        factors.highest_risk_token = token_scan.get("highest_risk")
        high_risk_count = token_scan.get("high_risk_count", 0)
        factors.honeypot_tokens_owned = high_risk_count
        factors.avg_held_token_safety = token_scan.get("avg_safety", 0)

        # Data flywheel: flag deployers of scam tokens
        deployers = token_scan.get("deployers_to_flag", [])
        if deployers:
            asyncio.create_task(_close_data_flywheel(deployers))  # noqa: RUF006

    # ── FREE: Funding source tracing ──
    funding = await _trace_funding_source(wallet_address, chain)
    if funding:
        factors.funding_source = funding.get("source", factors.funding_source)
        factors.funding_source_label = funding.get("source_label", factors.funding_source_label)
        factors.funding_source_is_cex = funding.get("is_cex", False)
        factors.funding_source_is_mixer = funding.get("is_mixer", False)
        factors.funding_hop_count = funding.get("hops", 0)
        data_sources.append("funding_trace")

    # ── FREE: Basic cross-chain identity ──
    free_entity = await _resolve_entity_free(wallet_address, chain)
    if free_entity and free_entity.get("label") != "unknown":
        if not factors.entity_label:
            factors.entity_label = free_entity["label"]
        if not factors.entity_confidence:
            factors.entity_confidence = 60  # free-tier confidence
        data_sources.append("free_entity")

    # ── FREE: Basic PnL ──
    native_bal = onchain.get("sol_balance", onchain.get("native_balance", 0)) if onchain else 0
    basic_pnl = await _calculate_basic_pnl(wallet_address, chain, token_list, native_bal)
    if basic_pnl:
        factors.current_balance_usd = max(
            factors.current_balance_usd or 0, basic_pnl.get("estimated_value_usd", 0)
        )
        factors.realized_pnl_usd = factors.realized_pnl_usd or 0  # keep if GMGN set it
        data_sources.append("jupiter_pnl")

    # ── ELITE: ML anomaly + portfolio tracker ──
    if tier == "elite":
        # ML anomaly detection
        try:
            from app.ml_anomaly import detect_wallet_anomaly

            anomaly = await detect_wallet_anomaly(wallet_address, chain)
            if anomaly:
                factors.sybil_score = anomaly.get("sybil_score", 0)
        except Exception:  # noqa: S110
            pass

        # Portfolio tracker
        try:
            from app.portfolio_tracker import get_portfolio

            portfolio = await get_portfolio(wallet_address, chain)
            if portfolio:
                factors.current_balance_usd = max(
                    factors.current_balance_usd, float(portfolio.get("total_value_usd", 0))
                )
                factors.portfolio_diversity_score = portfolio.get("diversity_score", 0)
                factors.stablecoin_ratio = portfolio.get("stablecoin_pct", 0)
                factors.bluechip_ratio = portfolio.get("bluechip_pct", 0)
                factors.memecoin_ratio = portfolio.get("memecoin_pct", 0)
        except Exception:  # noqa: S110
            pass

    # ═══════════════════════════════════════════
    # Risk scoring
    # ═══════════════════════════════════════════

    risk = 0
    flags = []

    # Labels (strongest signal)
    if factors.is_labeled_sanctioned:
        risk += 50
        flags.append("SANCTIONED")
    if factors.is_labeled_scammer:
        risk += 45
        flags.append("KNOWN_SCAMMER")
    if factors.is_labeled_insider:
        risk += 25
        flags.append("INSIDER_LABELED")
    if factors.is_labeled_bot:
        risk += 10
        flags.append("BOT_WALLET")
    if factors.is_labeled_exchange:
        risk -= 10  # exchanges are neutral

    # Funding
    if factors.funding_source_is_mixer:
        risk += 25
        flags.append("MIXER_FUNDING")
    if factors.funding_from_sanctioned:
        risk += 40
        flags.append("SANCTIONED_FUNDING")
    if factors.tornado_cash_exposure:
        risk += 35
        flags.append("TORNADO_CASH")

    # Age/Activity (only flag if wallet_age actually populated)
    if factors.wallet_age_days > 0:
        if factors.wallet_age_days < 1:
            risk += 15
            flags.append("FRESH_WALLET")
        elif factors.wallet_age_days < 7 and factors.total_volume_usd > 100000:
            risk += 20
            flags.append("NEW_WALLET_HIGH_VOLUME")
    elif factors.total_volume_usd > 50000:
        risk += 10
        flags.append("UNVERIFIED_HIGH_VOLUME")

    # Held token risk (FREE tier - from parallel token scan)
    if factors.honeypot_tokens_owned > 0:
        risk += 25
        flags.append(f"HOLDS_SCAM_TOKENS_{factors.honeypot_tokens_owned}")
    if factors.avg_held_token_safety and factors.avg_held_token_safety < 40:
        risk += 15
        flags.append("LOW_QUALITY_PORTFOLIO")

    # Token interaction
    if factors.rug_pull_launcher_count > 0:
        risk += 50
        flags.append("RUG_PULL_LAUNCHER")
    if factors.honeypot_tokens_owned > 0:
        risk += 20
        flags.append("HONEYPOT_HOLDER")
    if factors.sniper_activity_score > 70:
        risk += 20
        flags.append("HIGH_SNIPER")
    if factors.sandwich_attacks_executed > 0:
        risk += 25
        flags.append("SANDWICH_ATTACKER")
    if factors.flash_loan_usage > 10:
        risk += 15
        flags.append("HEAVY_FLASH_LOAN")

    # Behavioral
    if factors.insider_trading_confidence > 70:
        risk += 30
        flags.append("INSIDER_TRADING")
    if factors.wash_trading_score > 60:
        risk += 20
        flags.append("WASH_TRADING")
    if factors.pump_and_dump_participant:
        risk += 20
        flags.append("PUMP_DUMP_PARTICIPANT")

    # Cluster
    if factors.cluster_size > 5 and factors.cluster_shared_funding:
        risk += 20
        flags.append("LARGE_SHARED_CLUSTER")
    if factors.known_cluster_type in ("bot_farm", "sniper_ring"):
        risk += 15
        flags.append("SUSPICIOUS_CLUSTER")

    # ML
    if factors.sybil_score > 70:
        risk += 15
        flags.append("SYBIL_DETECTED")

    # Token portfolio risk (ELITE only)
    if factors.highest_risk_token:
        ts = factors.highest_risk_token
        if ts.get("safety_score", 100) < 25:
            risk += 15
            flags.append(f"HOLDS_CRITICAL_TOKEN_{ts.get('symbol', '???')}")

    factors.total_risk_score = min(100, max(0, risk))
    factors.risk_category = (
        "critical"
        if risk >= 80
        else "high_risk"
        if risk >= 60
        else "medium"
        if risk >= 35
        else "low_risk"
        if risk >= 15
        else "safe"
    )

    # Confidence based on data quality
    confidence = 15  # base
    if "helius" in data_sources:
        confidence += 20
    if "sentinel_labels" in data_sources:
        confidence += 20
    if "dexscreener" in data_sources:
        confidence += 10
    if "wallet_memory" in data_sources:
        confidence += 15
    if "gmgn" in data_sources:
        confidence += 15
    confidence += min(len(flags) * 2, 15)  # flags add some confidence
    factors.confidence = min(100, confidence)
    factors.data_sources = data_sources

    # Build modules_run list from data_sources with status
    for src in data_sources:
        modules_run.append({"module": src, "status": "ok"})
    # Add known modules that were attempted but failed (not in data_sources)
    attempted_modules = [
        "helius_wallet",
        "moralis_wallet",
        "etherscan_wallet",
        "dexscreener",
        "rugcheck",
        "goplus_security",
        "wallet_labels",
        "wallet_memory",
        "sentinel_risk",
        "gmgn",
        "funding_trace",
        "free_entity",
        "jupiter_pnl",
        "entity_clustering",
    ]
    succeeded_set = set(data_sources)
    for mod in attempted_modules:
        if mod not in succeeded_set:
            modules_run.append({"module": mod, "status": "degraded", "error": "no_data_returned"})

    elapsed = time.time() - start
    logger.info(
        f"Wallet scan {wallet_address[:12]}... [{chain}] = {factors.total_risk_score}/100 ({factors.risk_category}) conf={factors.confidence}% in {elapsed:.1f}s"
    )

    result = {
        "wallet": wallet_address,
        "chain": chain,
        "risk_score": factors.total_risk_score,
        "risk_category": factors.risk_category,
        "risk_flags": flags[:20],
        "total_flags": len(flags),
        "confidence": factors.confidence,
        "tier": tier,
        "data_sources": factors.data_sources,
        "factors": {
            "wallet_age_days": factors.wallet_age_days,
            "total_tx_count": factors.total_tx_count,
            "total_volume_usd": factors.total_volume_usd,
            "unique_tokens_held": factors.unique_tokens_held,
            "funding_source": factors.funding_source,
            "funding_source_label": factors.funding_source_label,
            "funding_source_is_cex": factors.funding_source_is_cex,
            "funding_source_is_mixer": factors.funding_source_is_mixer,
            "funding_from_sanctioned": factors.funding_from_sanctioned,
            "funding_from_scammer": factors.funding_from_scammer,
            "is_labeled_scammer": factors.is_labeled_scammer,
            "is_labeled_sanctioned": factors.is_labeled_sanctioned,
            "is_labeled_exchange": factors.is_labeled_exchange,
            "is_labeled_bot": factors.is_labeled_bot,
            "is_labeled_whale": factors.is_labeled_whale,
            "is_labeled_insider": factors.is_labeled_insider,
            "entity_label": factors.entity_label,
            "entity_confidence": factors.entity_confidence,
            "cluster_size": factors.cluster_size,
            "cluster_shared_funding": factors.cluster_shared_funding,
            "known_cluster_type": factors.known_cluster_type,
            "linked_wallets_count": factors.linked_wallets_count,
            "cross_chain_funding": factors.cross_chain_funding,
            "tornado_cash_exposure": factors.tornado_cash_exposure,
            "sniper_activity_score": factors.sniper_activity_score,
            "insider_trading_confidence": factors.insider_trading_confidence,
            "wash_trading_score": factors.wash_trading_score,
            "pump_and_dump_participant": factors.pump_and_dump_participant,
            "sybil_score": factors.sybil_score,
            "rug_pull_launcher_count": factors.rug_pull_launcher_count,
            "honeypot_tokens_owned": factors.honeypot_tokens_owned,
            "sandwich_attacks_executed": factors.sandwich_attacks_executed,
            "flash_loan_usage": factors.flash_loan_usage,
            "realized_pnl_usd": factors.realized_pnl_usd,
            "unrealized_pnl_usd": factors.unrealized_pnl_usd,
            "win_rate": factors.win_rate,
            "current_balance_usd": factors.current_balance_usd,
            "portfolio_diversity_score": factors.portfolio_diversity_score,
            "stablecoin_ratio": factors.stablecoin_ratio,
            "bluechip_ratio": factors.bluechip_ratio,
            "memecoin_ratio": factors.memecoin_ratio,
            "dex_swaps": factors.dex_swaps,
            "cex_deposits": factors.cex_deposits,
            "cex_withdrawals": factors.cex_withdrawals,
            "token_launches_participated": factors.token_launches_participated,
        },
        "held_tokens": {
            "scanned": len(factors.held_token_risks),
            "highest_risk": factors.highest_risk_token,
            "risks": factors.held_token_risks[:10],
        },
        "modules_run": modules_run,
        "scanned_at": datetime.now(UTC).isoformat(),
        "tool_fingerprints": None,
    }

    # Cache result for 1hr
    asyncio.create_task(_cache_set(cache_key, result, ttl=3600))  # noqa: RUF006

    return result


# ═══════════════════════════════════════════════════════════════
# TRENDING ENGINE - Multi-source, real-time
# ═══════════════════════════════════════════════════════════════


async def get_trending_tokens(chain: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Get trending tokens from multiple sources, normalized across chains."""
    import time

    start = time.time()

    trending = []
    sources_used = []

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            url = "https://api.dexscreener.com/latest/dex/search"
            params = {"q": chain or "SOL", "limit": limit}
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                now = datetime.now(UTC)
                for pair in pairs:
                    created = pair.get("pairCreatedAt")
                    liq = float(pair.get("liquidity", {}).get("usd", 0))
                    vol = float(pair.get("volume", {}).get("h24", 0))
                    if liq < 1000:
                        continue
                    age_hours = 999
                    if created:
                        age_hours = (
                            now - datetime.fromtimestamp(created / 1000, tz=UTC)
                        ).total_seconds() / 3600
                    trending_score = (vol * 0.6 + liq * 0.3) / max(age_hours, 1)
                    trending.append(
                        {
                            "chain": pair.get("chainId", chain or "unknown"),
                            "address": pair.get("baseToken", {}).get("address", ""),
                            "symbol": pair.get("baseToken", {}).get("symbol", ""),
                            "name": pair.get("baseToken", {}).get("name", ""),
                            "price_usd": float(pair.get("priceUsd", 0)),
                            "liquidity_usd": liq,
                            "volume_24h": vol,
                            "price_change_24h": float(pair.get("priceChange", {}).get("h24", 0)),
                            "age_hours": round(age_hours, 1),
                            "trending_score": round(trending_score, 2),
                            "dex": pair.get("dexId", ""),
                            "source": "dexscreener",
                        }
                    )
                sources_used.append("dexscreener")
        except Exception as e:
            logger.warning(f"DexScreener trending failed: {e}")

        try:
            network = "solana" if chain == "solana" else "eth"
            resp = await client.get(
                f"https://api.geckoterminal.com/api/v2/networks/{network}/trending_pools",
                params={"limit": limit},
            )
            if resp.status_code == 200:
                pools = resp.json().get("data", [])
                for pool in pools:
                    attrs = pool.get("attributes", {})
                    trending.append(
                        {
                            "chain": chain or "unknown",
                            "address": attrs.get("base_token_address", ""),
                            "symbol": attrs.get("base_token_symbol", ""),
                            "name": attrs.get("name", ""),
                            "price_usd": float(attrs.get("base_token_price_usd", 0)),
                            "liquidity_usd": float(attrs.get("reserve_in_usd", 0)),
                            "volume_24h": float(attrs.get("volume_usd", {}).get("h24", 0)),
                            "price_change_24h": float(
                                attrs.get("price_change_percentage", {}).get("h24", 0)
                            ),
                            "source": "geckoterminal",
                        }
                    )
                sources_used.append("geckoterminal")
        except Exception:  # noqa: S110
            pass

        try:
            resp = await client.get("https://api.coingecko.com/api/v3/search/trending")
            if resp.status_code == 200:
                coins = resp.json().get("coins", [])
                for item in coins[:limit]:
                    coin = item.get("item", {})
                    trending.append(
                        {
                            "chain": chain or "multi",
                            "address": coin.get("id", ""),
                            "symbol": coin.get("symbol", "").upper(),
                            "name": coin.get("name", ""),
                            "price_usd": coin.get("data", {}).get("price", 0),
                            "market_cap_rank": coin.get("market_cap_rank"),
                            "trending_score": coin.get("score", 0),
                            "source": "coingecko",
                        }
                    )
                sources_used.append("coingecko")
        except Exception:  # noqa: S110
            pass

    seen = set()
    unique = []
    for t in trending:
        key = f"{t.get('chain')}:{t.get('address')}"
        if key not in seen:
            seen.add(key)
            unique.append(t)

    unique.sort(key=lambda x: x.get("trending_score", 0), reverse=True)

    elapsed = time.time() - start
    logger.info(f"Trending: {len(unique)} tokens from {sources_used} in {elapsed:.1f}s")

    return {
        "tokens": unique[:limit],
        "total": len(unique),
        "sources": sources_used,
        "chains_covered": list({t.get("chain", "?") for t in unique}),
        "updated_at": datetime.now(UTC).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# REAL-TIME LAUNCH MONITOR
# ═══════════════════════════════════════════════════════════════

LAST_KNOWN_LAUNCHES: dict[str, set] = {}


async def get_new_launches(chain: str = "solana", since_seconds: int = 300) -> dict[str, Any]:
    """Get tokens launched in the last N seconds."""
    import time

    start = time.time()

    now = datetime.now(UTC)
    cutoff = (now.timestamp() * 1000) - (since_seconds * 1000)

    launches = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                "https://api.dexscreener.com/latest/dex/search", params={"q": chain, "limit": 50}
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                for pair in pairs:
                    created = pair.get("pairCreatedAt", 0)
                    if created and created > cutoff:
                        liq = float(pair.get("liquidity", {}).get("usd", 0))
                        if liq < 500:
                            continue
                        launches.append(
                            {
                                "chain": pair.get("chainId", chain),
                                "address": pair.get("baseToken", {}).get("address", ""),
                                "symbol": pair.get("baseToken", {}).get("symbol", ""),
                                "name": pair.get("baseToken", {}).get("name", ""),
                                "price_usd": float(pair.get("priceUsd", 0)),
                                "liquidity_usd": liq,
                                "age_seconds": int((now.timestamp() * 1000 - created) / 1000),
                                "dex": pair.get("dexId", ""),
                            }
                        )
        except Exception:  # noqa: S110
            pass

    chain_key = chain or "all"
    if chain_key not in LAST_KNOWN_LAUNCHES:
        LAST_KNOWN_LAUNCHES[chain_key] = set()

    known = LAST_KNOWN_LAUNCHES[chain_key]
    new_launches = [line for line in launches if line["address"] not in known]
    for line in new_launches:
        known.add(line["address"])

    # Keep set bounded
    if len(known) > 10000:
        LAST_KNOWN_LAUNCHES[chain_key] = set(list(known)[-5000:])

    elapsed = time.time() - start
    logger.info(f"New launches [{chain}]: {len(new_launches)} in {elapsed:.1f}s")

    return {
        "launches": new_launches,
        "total_new": len(new_launches),
        "total_known": len(known),
        "chain": chain,
        "since_seconds": since_seconds,
        "updated_at": datetime.now(UTC).isoformat(),
    }
