"""x402 market-side tools — launch radar, anomaly, chain health, MEV, gas.

Phase 3A of AUDIT-2026-Q3.md (split from app/routers/x402_tools.py).

Endpoints mounted on sub_router:
  GET  /api/v1/x402-tools/launch
  POST /api/v1/x402-tools/launch_intel
  POST /api/v1/x402-tools/anomaly
  POST /api/v1/x402-tools/market_overview
  POST /api/v1/x402-tools/chain_health
  POST /api/v1/x402-tools/defi_yield_scanner
  POST /api/v1/x402-tools/gas_forecast
  POST /api/v1/x402-tools/sniper_alert
  POST /api/v1/x402-tools/airdrop_finder
  POST /api/v1/x402-tools/mev_protection
"""
from __future__ import annotations

from datetime import datetime

import aiohttp
from fastapi import APIRouter, HTTPException

from app.billing.x402.shared import (
    FREE_RPCS,
    GenericRequest,
    fetch_with_fallback,
    record_x402_payment,
    rpc_call,
)

sub_router = APIRouter(tags=["x402-market-tools"])


# ═══════════════════════════════════════════════════════════════
# TOOL 4: Launch Radar ($0.50)
# ═══════════════════════════════════════════════════════════════


async def _detect_launches(chain: str, window_min: int) -> dict:
    """Detect new token launches with risk scoring."""
    result = {"chain": chain, "window_minutes": window_min, "sources_used": []}

    if chain == "solana":
        # Layer 1: Pump.fun new tokens
        try:
            data, _ = await fetch_with_fallback(
                [
                    "https://frontend-api.pump.fun/coins?offset=0&limit=50&sort=created_timestamp&order=desc"
                ]
            )
            if data:
                launches = []
                for coin in data:
                    mc = coin.get("usdMarketCap", 0)
                    vol = coin.get("totalVolume", 0)
                    score = 0
                    flags = []

                    if mc < 5000:
                        score += 30
                        flags.append("micro_cap")
                    if vol > mc * 5:
                        score += 20
                        flags.append("volume_spike")

                    launches.append(
                        {
                            "address": coin.get("mint"),
                            "name": coin.get("name"),
                            "symbol": coin.get("symbol"),
                            "market_cap": mc,
                            "volume": vol,
                            "risk_score": min(100, score),
                            "flags": flags,
                            "created": coin.get("createdTimestamp"),
                        }
                    )

                result["launches"] = launches
                result["sources_used"].append("pumpfun")
        except Exception:
            pass

        # Layer 2: Raydium new pools
        try:
            data, _ = await fetch_with_fallback(["https://api.raydium.io/v2/main/pairs"])
            if data and data.get("data"):
                result["raydium_pools"] = len(data["data"])
                result["sources_used"].append("raydium")
        except Exception:
            pass

    # Layer 3: DexScreener - new pairs
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/pairs/{chain}/recent"]
        )
        if data and data.get("pairs"):
            result["dexscreener_pairs"] = len(data["pairs"])
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    return result


@sub_router.get("/launch")
async def launch_radar(chain: str = "solana", window_minutes: int = 5):
    """New token launch detection with risk scoring."""
    try:
        result = await _detect_launches(chain, window_minutes)
        await record_x402_payment("launch", "0.03", f"api-{chain}")
        return {
            "tool": "Launch Radar",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 16: Launch Intel ($0.05)
# ═══════════════════════════════════════════════════════════════


async def _launch_intel(chain: str, hours: int) -> dict:
    """Token launch intelligence."""
    result = {"chain": chain, "hours": hours, "sources_used": []}
    launches = []

    # PumpFun new tokens
    if chain == "solana":
        try:
            data, _ = await fetch_with_fallback(
                [
                    "https://frontend-api.pump.fun/coins?offset=0&limit=20&sort=created_timestamp&order=desc"
                ]
            )
            if data and isinstance(data, list):
                for c in data[:10]:
                    launches.append(
                        {
                            "mint": c.get("mint"),
                            "name": c.get("name"),
                            "symbol": c.get("ticker"),
                            "market_cap": c.get("usdMarketCap", 0),
                            "source": "pumpfun",
                        }
                    )
                result["sources_used"].append("pumpfun")
        except Exception:
            pass

    # DexScreener trending
    try:
        data, _ = await fetch_with_fallback(["https://api.dexscreener.com/latest/dex/search?q="])
        if data and data.get("pairs"):
            trending = sorted(
                data["pairs"], key=lambda p: p.get("volume", {}).get("h24", 0), reverse=True
            )[:5]
            for p in trending:
                launches.append(
                    {
                        "address": p.get("baseToken", {}).get("address"),
                        "symbol": p.get("baseToken", {}).get("symbol"),
                        "volume_24h": p.get("volume", {}).get("h24", 0),
                        "source": "dexscreener",
                    }
                )
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    result["new_launches"] = launches
    result["total_found"] = len(launches)
    return result


@sub_router.post("/launch_intel")
async def launch_intel(req: GenericRequest):
    """Token launch intelligence."""
    try:
        result = await _launch_intel(req.chain, req.hours)
        await record_x402_payment("launch_intel", "0.05", "scan")
        return {
            "tool": "Launch Intelligence",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 17: Anomaly Detector ($0.08)
# ═══════════════════════════════════════════════════════════════


async def _anomaly_detector(chain: str) -> dict:
    """Market anomaly detection."""
    result = {"chain": chain, "anomalies": [], "sources_used": []}

    # Get market overview for comparison
    try:
        data, _ = await fetch_with_fallback(["https://api.coingecko.com/api/v3/global"])
        if data and data.get("data"):
            global_data = data["data"]
            result["total_market_cap"] = global_data.get("total_market_cap", {}).get("usd", 0)
            result["market_cap_change_24h"] = global_data.get(
                "market_cap_change_percentage_24h_usd", 0
            )
            result["sources_used"].append("coingecko")
    except Exception:
        pass

    # DexScreener for volume spikes
    try:
        data, _ = await fetch_with_fallback(["https://api.dexscreener.com/latest/dex/search?q="])
        if data and data.get("pairs"):
            for p in data["pairs"][:20]:
                vol_h24 = p.get("volume", {}).get("h24", 0)
                p.get("volume", {}).get("h6", 0)
                liq = p.get("liquidity", {}).get("usd", 0)
                if liq > 0 and vol_h24 > liq * 5:
                    result["anomalies"].append(
                        {
                            "type": "volume_spike",
                            "token": p.get("baseToken", {}).get("symbol"),
                            "address": p.get("baseToken", {}).get("address"),
                            "volume_24h": vol_h24,
                            "liquidity": liq,
                            "ratio": vol_h24 / liq if liq > 0 else 0,
                        }
                    )
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    result["anomaly_count"] = len(result["anomalies"])
    result["market_health"] = (
        "normal"
        if result["anomaly_count"] < 3
        else "elevated"
        if result["anomaly_count"] < 7
        else "critical"
    )

    return result


@sub_router.post("/anomaly")
async def anomaly_detector(req: GenericRequest):
    """Market anomaly detector."""
    try:
        chain = req.chain or "all"
        result = await _anomaly_detector(chain)
        await record_x402_payment("anomaly", "0.08", chain)
        return {
            "tool": "Anomaly Detector",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 19: Market Overview ($0.05)
# ═══════════════════════════════════════════════════════════════


async def _market_overview(chain: str) -> dict:
    """Comprehensive market overview."""
    result = {"chain": chain, "sources_used": []}

    # CoinGecko global
    try:
        data, _ = await fetch_with_fallback(["https://api.coingecko.com/api/v3/global"])
        if data and data.get("data"):
            gd = data["data"]
            result["total_market_cap_usd"] = gd.get("total_market_cap", {}).get("usd", 0)
            result["total_volume_24h"] = gd.get("total_volume", {}).get("usd", 0)
            result["btc_dominance"] = gd.get("market_cap_percentage", {}).get("btc", 0)
            result["eth_dominance"] = gd.get("market_cap_percentage", {}).get("eth", 0)
            result["active_cryptocurrencies"] = gd.get("active_cryptocurrencies", 0)
            result["sources_used"].append("coingecko")
    except Exception:
        pass

    # CoinGecko top coins
    try:
        data, _ = await fetch_with_fallback(
            [
                "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=10&page=1"
            ]
        )
        if data:
            result["top_coins"] = [
                {
                    "symbol": c.get("symbol"),
                    "price": c.get("current_price"),
                    "market_cap": c.get("market_cap"),
                    "change_24h": c.get("price_change_percentage_24h"),
                }
                for c in data
            ]
            result["sources_used"].append("coingecko_markets")
    except Exception:
        pass

    # DeFiLlama TVL
    try:
        data, _ = await fetch_with_fallback(["https://api.llama.fi/v2/chains"])
        if data:
            result["chain_tvls"] = {c.get("name"): c.get("tvl") for c in data[:10]}
            result["sources_used"].append("defillama")
    except Exception:
        pass

    return result


@sub_router.post("/market_overview")
async def market_overview(req: GenericRequest):
    """Comprehensive market overview."""
    try:
        result = await _market_overview(req.chain)
        await record_x402_payment("market_overview", "0.05", "overview")
        return {
            "tool": "Market Overview",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 21: Chain Health ($0.05)
# ═══════════════════════════════════════════════════════════════


async def _chain_health(chain: str) -> dict:
    """Chain health metrics."""
    result = {"chain": chain, "chains": {}, "sources_used": []}

    chains_to_check = ["solana", "ethereum", "base", "bsc"] if chain == "all" else [chain]

    # DeFiLlama TVL per chain
    try:
        data, _ = await fetch_with_fallback(["https://api.llama.fi/v2/chains"])
        if data:
            chain_map = {"solana": "Solana", "ethereum": "Ethereum", "base": "Base", "bsc": "BSC"}
            for c in data:
                name = c.get("name")
                for key, val in chain_map.items():
                    if key in chains_to_check and val.lower() in name.lower():
                        result["chains"][key] = {
                            "tvl": c.get("tvl", 0),
                            "protocols": c.get("protocols", 0),
                            "chain_id": c.get("chainId"),
                        }
            result["sources_used"].append("defillama")
    except Exception:
        pass

    # RPC health check
    for c in chains_to_check:
        rpcs = FREE_RPCS.get(c, [])
        healthy = 0
        for rpc in rpcs[:2]:
            try:
                async with aiohttp.ClientSession() as session, session.post(
                    rpc,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_blockNumber" if c != "solana" else "getBlockHeight",
                        "params": [],
                    },
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        healthy += 1
            except Exception:
                pass
        result["chains"].setdefault(c, {})["rpc_healthy"] = healthy

    return result


@sub_router.post("/chain_health")
async def chain_health(req: GenericRequest):
    """Chain health metrics."""
    try:
        result = await _chain_health(req.chain)
        await record_x402_payment("chain_health", "0.05", req.chain)
        return {
            "tool": "Chain Health",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 27: DeFi Yield Scanner ($0.08)
# ═══════════════════════════════════════════════════════════════


async def _defi_yield_scanner(chain: str) -> dict:
    """DeFi yield scanner."""
    result = {"chain": chain, "sources_used": [], "pools": []}

    # DeFiLlama yields
    try:
        data, _ = await fetch_with_fallback(["https://yields.llama.fi/pools"])
        if data and data.get("data"):
            pools = sorted(data["data"], key=lambda p: p.get("apy", 0), reverse=True)[:20]
            for p in pools:
                result["pools"].append(
                    {
                        "chain": p.get("chain"),
                        "project": p.get("project"),
                        "symbol": p.get("symbol"),
                        "tvl": p.get("tvlUsd", 0),
                        "apy": p.get("apy", 0),
                        "apy_base": p.get("apyBase", 0),
                        "apy_reward": p.get("apyReward", 0),
                    }
                )
            result["sources_used"].append("defillama")
    except Exception:
        pass

    result["total_pools"] = len(result["pools"])
    result["highest_apy"] = result["pools"][0]["apy"] if result["pools"] else 0

    return result


@sub_router.post("/defi_yield_scanner")
async def defi_yield_scanner(req: GenericRequest):
    """DeFi yield scanner."""
    try:
        result = await _defi_yield_scanner(req.chain)
        await record_x402_payment("defi_yield_scanner", "0.08", "scan")
        return {
            "tool": "DeFi Yield Scanner",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 30: Gas Forecast ($0.05)
# ═══════════════════════════════════════════════════════════════


async def _gas_forecast(chain: str) -> dict:
    """Gas price forecast."""
    result = {"chain": chain, "sources_used": []}

    # EVM gas prices
    if chain in ["base", "ethereum", "bsc"]:
        try:
            data = await rpc_call(chain, "eth_gasPrice", [])
            if data:
                gas_wei = int(data, 16)
                gas_gwei = gas_wei / 1e9
                result["current_gas_gwei"] = gas_gwei
                result["current_gas_usd_estimate"] = gas_gwei * 0.001  # Rough estimate
                result["sources_used"].append(f"{chain}_rpc")
        except Exception:
            pass

    # Solana compute units
    elif chain == "solana":
        try:
            result["sol_compute_unit_price"] = "dynamic (based on priority fees)"
            result["sources_used"].append("solana_docs")
        except Exception:
            pass

    # Blocknative gas station (free tier)
    try:
        data, _ = await fetch_with_fallback(["https://api.blocknative.com/gasprices"])
        if data and data.get("estimatedPrices"):
            result["blocknative"] = data["estimatedPrices"]
            result["sources_used"].append("blocknative")
    except Exception:
        pass

    return result


@sub_router.post("/gas_forecast")
async def gas_forecast(req: GenericRequest):
    """Gas price forecast."""
    try:
        result = await _gas_forecast(req.chain)
        await record_x402_payment("gas_forecast", "0.05", req.chain)
        return {
            "tool": "Gas Forecast",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 31: Sniper Alert ($0.05)
# ═══════════════════════════════════════════════════════════════


async def _sniper_alert(chain: str, hours: int) -> dict:
    """Sniper bot detection and alerts."""
    result = {"chain": chain, "hours": hours, "sources_used": [], "sniper_activity": []}

    # PumpFun for new tokens (sniper targets)
    if chain == "solana":
        try:
            data, _ = await fetch_with_fallback(
                [
                    "https://frontend-api.pump.fun/coins?offset=0&limit=10&sort=created_timestamp&order=desc"
                ]
            )
            if data and isinstance(data, list):
                for c in data[:5]:
                    result["sniper_activity"].append(
                        {
                            "token": c.get("name"),
                            "mint": c.get("mint"),
                            "age_minutes": "recent",
                            "sniper_risk": "high"
                            if c.get("usdMarketCap", 0) > 100000
                            else "medium",
                        }
                    )
                result["sources_used"].append("pumpfun")
        except Exception:
            pass

    # DexScreener for abnormal early trading
    try:
        data, _ = await fetch_with_fallback(["https://api.dexscreener.com/latest/dex/search?q="])
        if data and data.get("pairs"):
            for p in data["pairs"][:10]:
                txns = p.get("txns", {}).get("m5", {})
                buys = txns.get("buys", 0)
                if buys > 20:
                    result["sniper_activity"].append(
                        {
                            "token": p.get("baseToken", {}).get("symbol"),
                            "address": p.get("baseToken", {}).get("address"),
                            "buys_5m": buys,
                            "sniper_risk": "high",
                            "source": "dexscreener",
                        }
                    )
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    result["total_alerts"] = len(result["sniper_activity"])
    return result


@sub_router.post("/sniper_alert")
async def sniper_alert(req: GenericRequest):
    """Sniper bot detection."""
    try:
        result = await _sniper_alert(req.chain, req.hours)
        await record_x402_payment("sniper_alert", "0.05", req.chain)
        return {
            "tool": "Sniper Alert",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 34: Airdrop Finder ($0.05)
# ═══════════════════════════════════════════════════════════════


async def _airdrop_finder(chain: str) -> dict:
    """Airdrop opportunity finder."""
    result = {"chain": chain, "sources_used": [], "opportunities": []}

    # DeFiLlama for new protocols (potential airdrops)
    try:
        data, _ = await fetch_with_fallback(["https://api.llama.fi/protocols"])
        if data:
            # Filter for protocols without tokens
            no_token = [p for p in data if not p.get("token") and p.get("tvl", 0) > 10000000][:10]
            for p in no_token:
                result["opportunities"].append(
                    {
                        "name": p.get("name"),
                        "category": p.get("category"),
                        "tvl": p.get("tvl", 0),
                        "chains": p.get("chains", []),
                        "airdrop_potential": "high" if p.get("tvl", 0) > 100000000 else "medium",
                    }
                )
            result["sources_used"].append("defillama")
    except Exception:
        pass

    result["total_opportunities"] = len(result["opportunities"])
    return result


@sub_router.post("/airdrop_finder")
async def airdrop_finder(req: GenericRequest):
    """Airdrop opportunity finder."""
    try:
        result = await _airdrop_finder(req.chain)
        await record_x402_payment("airdrop_finder", "0.05", "scan")
        return {
            "tool": "Airdrop Finder",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 35: MEV Protection ($0.08)
# ═══════════════════════════════════════════════════════════════


async def _mev_protection(chain: str) -> dict:
    """MEV protection analysis."""
    result = {"chain": chain, "sources_used": [], "mev_stats": {}}

    if chain == "solana":
        # Jito MEV stats
        try:
            data, _ = await fetch_with_fallback(
                ["https://stats.jito.network/api/v1/bundles?limit=10"]
            )
            if data:
                result["mev_stats"]["jito"] = True
                result["sources_used"].append("jito")
        except Exception:
            pass

        # Check for MEV bots in recent blocks
        try:
            slot = await rpc_call("solana", "getSlot", [])
            if slot:
                result["current_slot"] = slot
                result["mev_stats"]["slot"] = slot
                result["sources_used"].append("solana_rpc")
        except Exception:
            pass

    elif chain in ["base", "ethereum", "bsc"]:
        # Flashbots stats
        try:
            data, _ = await fetch_with_fallback(["https://api.flashbots.net/stats"])
            if data:
                result["mev_stats"]["flashbots"] = True
                result["sources_used"].append("flashbots")
        except Exception:
            pass

        # Gas price for MEV detection
        try:
            gas = await rpc_call(chain, "eth_gasPrice", [])
            if gas:
                result["gas_price_gwei"] = int(gas, 16) / 1e9
                result["sources_used"].append(f"{chain}_rpc")
        except Exception:
            pass

    result["mev_risk"] = (
        "high" if chain == "ethereum" else "medium" if chain in ["base", "bsc"] else "low"
    )
    result["protection_tips"] = [
        "Use private RPC endpoints for large trades",
        "Set appropriate slippage tolerance",
        "Avoid trading during high volatility periods",
        "Use MEV-protected order routing",
    ]

    return result


@sub_router.post("/mev_protection")
async def mev_protection(req: GenericRequest):
    """MEV protection analysis."""
    try:
        result = await _mev_protection(req.chain)
        await record_x402_payment("mev_protection", "0.08", req.chain)
        return {
            "tool": "MEV Protection",
            "version": "2.0",
            "timestamp": datetime.utcnow().isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
