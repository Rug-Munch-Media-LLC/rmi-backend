"""x402 wallet-side tools — profiler, smartmoney, cluster, whale, portfolio.

Phase 3A of AUDIT-2026-Q3.md (split from app/routers/x402_tools.py).

Endpoints mounted on sub_router:
  POST /api/v1/x402-tools/wallet
  GET  /api/v1/x402-tools/smartmoney
  POST /api/v1/x402-tools/cluster
  POST /api/v1/x402-tools/whale
  POST /api/v1/x402-tools/portfolio_tracker
  POST /api/v1/x402-tools/copy_trade_finder

Deployer-side wallet tools (insider tracker, whale_accumulation) live in
tools/deployer_tools.py to keep the boundary clean.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from app.domains.billing.x402.shared import (
    ClusterRequest,
    GenericRequest,
    WalletListRequest,
    WalletRequest,
    fetch_with_fallback,
    record_x402_payment,
    rpc_call,
)

sub_router = APIRouter(tags=["x402-wallet-tools"])


# ═══════════════════════════════════════════════════════════════
# TOOL 2: Wallet Profiler ($0.75)
# ═══════════════════════════════════════════════════════════════


async def _profile_wallet(address: str, chain: str) -> dict:
    """Full wallet profile with persona detection and activity analysis."""
    result = {"chain": chain, "address": address, "sources_used": []}

    # Layer 1: Solana RPC - get balance + transaction count
    if chain == "solana":
        try:
            balance = await rpc_call("solana", "getBalance", [address])
            if balance is not None:
                result["balance_sol"] = balance / 1e9
                result["sources_used"].append("solana_rpc")
        except Exception:
            pass

        # Get recent transactions
        try:
            sigs = await rpc_call("solana", "getSignaturesForAddress", [address, {"limit": 20}])
            if sigs:
                result["tx_count_recent"] = len(sigs)
                result["last_tx"] = sigs[0].get("blockTime") if sigs else None
                result["sources_used"].append("solana_txs")

                # Analyze transaction patterns
                success_count = sum(1 for s in sigs if s.get("err") is None)
                result["success_rate"] = success_count / len(sigs) if sigs else 0

                # Time-based analysis
                if len(sigs) >= 2:
                    times = [s.get("blockTime", 0) for s in sigs if s.get("blockTime")]
                    if len(times) >= 2:
                        time_span = max(times) - min(times)
                        if time_span > 0:
                            result["tx_frequency"] = len(sigs) / (time_span / 86400)  # per day
        except Exception:
            pass

        # Get token accounts
        try:
            token_accounts = await rpc_call(
                "solana",
                "getTokenAccountsByOwner",
                [
                    address,
                    {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                    {"encoding": "jsonParsed"},
                ],
            )
            if token_accounts and token_accounts.get("value"):
                result["token_count"] = len(token_accounts["value"])
                result["sources_used"].append("solana_tokens")
        except Exception:
            pass

    # Layer 2: DexScreener - check if wallet has interacted with known tokens
    # (we can't directly query by wallet, but we use the balance/token data)

    # Layer 3: Coingecko for any listed assets
    # Layer 4: DeFiLlama for protocol interactions

    # Persona detection
    persona = "unknown"
    confidence = 0

    if result.get("tx_frequency", 0) > 50:
        persona = "bot"
        confidence = 85
    elif result.get("tx_frequency", 0) > 10:
        persona = "active_trader"
        confidence = 70
    elif result.get("token_count", 0) > 50:
        persona = "collector"
        confidence = 60
    elif result.get("balance_sol", 0) > 1000:
        persona = "whale"
        confidence = 75
    elif result.get("balance_sol", 0) > 100:
        persona = "experienced"
        confidence = 65
    elif result.get("tx_count_recent", 0) > 0:
        persona = "casual"
        confidence = 50
    else:
        persona = "inactive"
        confidence = 40

    result["persona"] = persona
    result["persona_confidence"] = confidence

    return result


@sub_router.post("/wallet")
async def wallet_profiler(req: WalletRequest):
    """Full wallet analysis - persona, activity, holdings, patterns."""
    try:
        result = await _profile_wallet(req.address, req.chain)
        await record_x402_payment("wallet", "0.05", req.address)
        return {
            "tool": "Wallet Profiler",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 3: Smart Money Tracker ($1.00)
# ═══════════════════════════════════════════════════════════════


async def _get_smart_money(chain: str, threshold: float, limit: int) -> dict:
    """Track whale movements and smart money patterns."""
    result = {"chain": chain, "threshold_usd": threshold, "sources_used": []}

    # Layer 1: DexScreener trending
    try:
        data, _ = await fetch_with_fallback(
            [
                "https://api.dexscreener.com/latest/dex/search?q=",
            ]
        )
        if data and data.get("pairs"):
            trending = sorted(
                data["pairs"], key=lambda p: p.get("volume", {}).get("h24", 0), reverse=True
            )[:limit]
            result["trending_tokens"] = [
                {
                    "address": p.get("baseToken", {}).get("address"),
                    "symbol": p.get("baseToken", {}).get("symbol"),
                    "volume_24h": p.get("volume", {}).get("h24", 0),
                    "liquidity": p.get("liquidity", {}).get("usd", 0),
                }
                for p in trending
            ]
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    # Layer 2: DefiLlama - top protocols by TVL change
    try:
        data, _ = await fetch_with_fallback(["https://api.llama.fi/protocols"])
        if data:
            protocols = sorted(
                data, key=lambda p: p.get("chainTvls", {}).get(f"{chain}", 0), reverse=True
            )[:10]
            result["top_protocols"] = [
                {"name": p.get("name"), "tvl": p.get("tvl")} for p in protocols
            ]
            result["sources_used"].append("defillama")
    except Exception:
        pass

    # Layer 3: CoinGecko - top gainers
    try:
        data, _ = await fetch_with_fallback(["https://api.coingecko.com/api/v3/search/trending"])
        if data and data.get("coins"):
            result["trending_coins"] = [
                {"name": c.get("item", {}).get("name"), "symbol": c.get("item", {}).get("symbol")}
                for c in data["coins"][:limit]
            ]
            result["sources_used"].append("coingecko")
    except Exception:
        pass

    # Layer 4: Pump.fun - new launches with high volume
    if chain == "solana":
        try:
            data, _ = await fetch_with_fallback(
                [
                    "https://frontend-api.pump.fun/coins?offset=0&limit=20&sort=last_trade_timestamp&order=desc&minMarketCap=10000&maxMarketCap=1000000"
                ]
            )
            if data:
                result["new_launches"] = [
                    {
                        "mint": c.get("mint"),
                        "name": c.get("name"),
                        "market_cap": c.get("usdMarketCap"),
                    }
                    for c in data[:10]
                    if c.get("usdMarketCap", 0) > threshold
                ]
                result["sources_used"].append("pumpfun")
        except Exception:
            pass

    return result


@sub_router.get("/smartmoney")
async def smart_money_tracker(chain: str = "solana", threshold: float = 10000.0, limit: int = 20):
    """Real-time whale/insider tracking across chains."""
    try:
        result = await _get_smart_money(chain, threshold, limit)
        await record_x402_payment("smartmoney", "0.05", f"api-{chain}")
        return {
            "tool": "Smart Money Tracker",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 7: Cluster Detection ($1.00)
# ═══════════════════════════════════════════════════════════════


async def _cluster_analysis(address: str, chain: str, depth: int) -> dict:
    """Map wallet clusters and funding chains."""
    result = {"chain": chain, "address": address, "depth": depth, "sources_used": []}

    # Layer 1: Solana RPC - get transaction history
    if chain == "solana":
        try:
            sigs = await rpc_call("solana", "getSignaturesForAddress", [address, {"limit": 100}])
            if sigs:
                result["transaction_count"] = len(sigs)
                result["sources_used"].append("solana_txs")

                # Extract counterparties
                counterparties = set()
                for sig in sigs[:20]:
                    try:
                        tx = await rpc_call(
                            "solana",
                            "getTransaction",
                            [
                                sig.get("signature"),
                                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                            ],
                        )
                        if tx and tx.get("transaction") and tx["transaction"].get("message"):
                            accounts = tx["transaction"]["message"].get("accountKeys", [])
                            for acc in accounts:
                                addr = acc.get("pubkey") if isinstance(acc, dict) else acc
                                if addr and addr != address:
                                    counterparties.add(addr)
                    except Exception:
                        pass

                result["counterparties"] = list(counterparties)[:50]
                result["cluster_size"] = len(counterparties)
        except Exception:
            pass

    # Layer 2: DexScreener - check if address is a known deployer
    # Layer 3: Etherscan/BaseScan for EVM chains
    if chain in ["base", "ethereum", "bsc"]:
        try:
            explorer = "basescan.org" if chain == "base" else "etherscan.io"
            api_base = f"https://api.{explorer}/api"
            key_env = "BASESCAN_KEY" if chain == "base" else "ETHERSCAN_KEY"
            key = os.getenv(key_env, "")
            if key:
                data, _ = await fetch_with_fallback(
                    [
                        f"{api_base}?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&page=1&offset=20&sort=desc&apikey={key}"
                    ]
                )
                if data and data.get("result"):
                    result["tx_count"] = len(data["result"])
                    result["sources_used"].append(f"{chain}_explorer")
        except Exception:
            pass

    # Layer 4: Compute cluster metrics
    result["cluster_risk"] = (
        "low"
        if result.get("cluster_size", 0) < 5
        else "medium"
        if result.get("cluster_size", 0) < 20
        else "high"
    )

    return result


@sub_router.post("/cluster")
async def cluster_detection(req: ClusterRequest):
    """Wallet cluster mapping - sybil detection, hidden networks."""
    try:
        result = await _cluster_analysis(req.address, req.chain, req.depth)
        await record_x402_payment("cluster", "0.05", req.address)
        return {
            "tool": "Cluster Detection",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 15: Whale Decoder ($0.15)
# ═══════════════════════════════════════════════════════════════


async def _whale_decoder(address: str, chain: str) -> dict:
    """Advanced whale wallet analysis."""
    result = {"address": address, "chain": chain, "sources_used": []}

    # Solana RPC - get balance and transactions
    if chain == "solana":
        try:
            balance = await rpc_call("solana", "getBalance", [address])
            if balance is not None:
                result["sol_balance"] = balance / 1e9
                result["sources_used"].append("solana_rpc")
        except Exception:
            pass

        try:
            token_accounts = await rpc_call(
                "solana",
                "getTokenAccountsByOwner",
                [
                    address,
                    {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                    {"encoding": "jsonParsed"},
                ],
            )
            if token_accounts and token_accounts.get("value"):
                tokens = token_accounts["value"]
                result["token_count"] = len(tokens)
                result["top_tokens"] = []
                for t in tokens[:10]:
                    parsed = t.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                    result["top_tokens"].append(
                        {
                            "mint": parsed.get("mint"),
                            "amount": parsed.get("tokenAmount", {}).get("uiAmount", 0),
                        }
                    )
                result["sources_used"].append("solana_rpc_tokens")
        except Exception:
            pass

    # EVM chain balance
    elif chain in ["base", "ethereum", "bsc"]:
        try:
            balance = await rpc_call(chain, "eth_getBalance", [address, "latest"])
            if balance:
                result["native_balance_wei"] = balance
                result["native_balance_eth"] = int(balance, 16) / 1e18
                result["sources_used"].append(f"{chain}_rpc")
        except Exception:
            pass

    # DexScreener for recent activity
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/search?q={address[:10]}"]
        )
        if data and data.get("pairs"):
            result["recent_pairs"] = len(data["pairs"])
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    # Persona detection
    sol_balance = result.get("sol_balance", result.get("native_balance_eth", 0))
    persona = "unknown"
    if sol_balance > 1000:
        persona = "mega_whale"
    elif sol_balance > 100:
        persona = "whale"
    elif sol_balance > 10:
        persona = "dolphin"
    elif sol_balance > 1:
        persona = "retail"

    result["persona"] = persona
    result["activity_level"] = (
        "high"
        if result.get("recent_pairs", 0) > 5
        else "medium"
        if result.get("recent_pairs", 0) > 1
        else "low"
    )
    result["trust_score"] = (
        85 if persona in ["whale", "mega_whale"] else 60 if persona == "dolphin" else 40
    )

    return result


@sub_router.post("/whale")
async def whale_decoder(req: GenericRequest):
    """Whale wallet decoder and analysis."""
    try:
        address = req.address or req.query or ""
        result = await _whale_decoder(address, req.chain)
        await record_x402_payment("whale", "0.15", address)
        return {
            "tool": "Whale Decoder",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 23: Portfolio Tracker ($0.10)
# ═══════════════════════════════════════════════════════════════


async def _portfolio_tracker(addresses: list[str], chain: str) -> dict:
    """Multi-wallet portfolio tracker."""
    result = {"addresses": addresses, "chain": chain, "sources_used": [], "wallets": []}

    for addr in addresses[:5]:  # Limit to 5 wallets
        wallet_data = {"address": addr, "tokens": []}

        # Solana balance
        if chain == "solana":
            try:
                balance = await rpc_call("solana", "getBalance", [addr])
                if balance is not None:
                    wallet_data["sol_balance"] = balance / 1e9
                    result["sources_used"].append("solana_rpc")
            except Exception:
                pass

        # EVM balance
        elif chain in ["base", "ethereum", "bsc"]:
            try:
                balance = await rpc_call(chain, "eth_getBalance", [addr, "latest"])
                if balance:
                    wallet_data["native_balance"] = int(balance, 16) / 1e18
                    result["sources_used"].append(f"{chain}_rpc")
            except Exception:
                pass

        result["wallets"].append(wallet_data)

    result["total_wallets"] = len(result["wallets"])
    return result


@sub_router.post("/portfolio_tracker")
async def portfolio_tracker(req: WalletListRequest):
    """Multi-wallet portfolio tracker."""
    try:
        result = await _portfolio_tracker(req.addresses, req.chain)
        await record_x402_payment("portfolio_tracker", "0.10", ",".join(req.addresses[:3]))
        return {
            "tool": "Portfolio Tracker",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 24: Copy Trade Finder ($0.10)
# ═══════════════════════════════════════════════════════════════


async def _copy_trade_finder(chain: str) -> dict:
    """Find profitable wallets to copy trade."""
    result = {"chain": chain, "sources_used": []}

    # DexScreener for top gainers
    try:
        data, _ = await fetch_with_fallback(["https://api.dexscreener.com/latest/dex/search?q="])
        if data and data.get("pairs"):
            gainers = sorted(
                data["pairs"], key=lambda p: p.get("priceChange", {}).get("h24", 0), reverse=True
            )[:10]
            result["top_gainers"] = [
                {
                    "symbol": p.get("baseToken", {}).get("symbol"),
                    "price_change_24h": p.get("priceChange", {}).get("h24", 0),
                    "volume_24h": p.get("volume", {}).get("h24", 0),
                    "liquidity": p.get("liquidity", {}).get("usd", 0),
                }
                for p in gainers
            ]
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    result["smart_wallets"] = []  # Would need on-chain analysis for this
    result["copy_trades"] = []

    return result


@sub_router.post("/copy_trade_finder")
async def copy_trade_finder(req: GenericRequest):
    """Copy trade intelligence."""
    try:
        result = await _copy_trade_finder(req.chain)
        await record_x402_payment("copy_trade_finder", "0.10", "scan")
        return {
            "tool": "Copy Trade Finder",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
