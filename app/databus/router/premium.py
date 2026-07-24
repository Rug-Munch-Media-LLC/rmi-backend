"""DataBus Router sub-module. Premium endpoints."""

from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query, Request
from typing import Any
from app.core.logging import get_logger
from app.databus.core import databus

logger = get_logger("databus.router.premium")
router = APIRouter(tags=["databus"])

@router.get("/premium/bundles/{address}")
async def premium_bundle_detect(address: str, chain: str = "solana", request: Request = None) -> Any:
    """Bundle detection (RugMaps cluster analysis). Premium tier."""
    return await databus.fetch("bundle_detect", address=address, chain=chain)

@router.get("/premium/clusters/{address}")
async def premium_cluster_map(
    address: str, chain: str = "solana", depth: int = 3, request: Request = None
) -> Any:
    """Full wallet cluster mapping with graph-ready nodes/edges. Premium tier."""
    return await databus.fetch("cluster_map", address=address, chain=chain, depth=depth)

@router.get("/premium/dev-finder/{token}")
async def premium_dev_finder(token: str, chain: str = "solana", request: Request = None) -> Any:
    """Find developer/creator wallets behind a token. Premium tier."""
    return await databus.fetch("dev_finder", token_address=token, chain=chain)

@router.get("/premium/snipers/{address}")
async def premium_sniper_detect(address: str, chain: str = "solana", request: Request = None) -> Any:
    """Detect snipers - first-block buyers with fast dumps. Premium tier."""
    return await databus.fetch("sniper_detect", address=address, chain=chain)

@router.get("/premium/bot-farms/{address}")
async def premium_bot_farms(address: str, chain: str = "solana", request: Request = None) -> Any:
    """Detect bot farms - identical behavior patterns. Premium tier."""
    return await databus.fetch("bot_farm_detect", address=address, chain=chain)

@router.get("/premium/copy-trading/{address}")
async def premium_copy_trading(address: str, chain: str = "solana", request: Request = None) -> Any:
    """Detect copy trading patterns. Premium tier."""
    return await databus.fetch("copy_trade_detect", address=address, chain=chain)

@router.get("/premium/insider-signals/{address}")
async def premium_insider(address: str, chain: str = "solana", request: Request = None) -> Any:
    """Detect insider trading signals. Premium tier."""
    return await databus.fetch("insider_detect", address=address, chain=chain)

@router.get("/premium/wash-trading/{address}")
async def premium_wash_trading(address: str, chain: str = "solana", request: Request = None) -> Any:
    """Detect wash trading patterns. Premium tier."""
    return await databus.fetch("wash_trade_detect", address=address, chain=chain)

@router.get("/premium/mev/{address}")
async def premium_mev(address: str, chain: str = "solana", request: Request = None) -> Any:
    """Detect MEV sandwich attacks. Premium tier."""
    return await databus.fetch("mev_detect", address=address, chain=chain)

@router.get("/premium/fresh-wallets/{address}")
async def premium_fresh_wallets(address: str, chain: str = "solana", request: Request = None) -> Any:
    """Analyze fresh wallet concentration - high new-wallet % = rug risk. Premium tier."""
    return await databus.fetch("fresh_wallet_analysis", address=address, chain=chain)

@router.get("/premium/volume-authenticity/{address}")
async def premium_volume_auth(address: str, chain: str = "ethereum", request: Request = None) -> Any:
    """Fake volume % with bootstrap CI. The RugCharts moat - no one else does this."""
    return await databus.fetch(
        "volume_authenticity",
        address=address,
        chain=chain,
        volume_24h=request.query_params.get("volume_24h", "0"),
        liquidity_usd=request.query_params.get("liquidity_usd", "0"),
        unique_wallets=request.query_params.get("unique_wallets", "0"),
        buy_count=request.query_params.get("buy_count", "0"),
        sell_count=request.query_params.get("sell_count", "0"),
        tx_count=request.query_params.get("tx_count", "0"),
    )

@router.get("/ohlcv/{address}")
async def ohlcv_candles(
    address: str,
    chain: str = "ethereum",
    timeframe: str = "1h",
    limit: int = 100,
    request: Request = None,
) -> Any:
    """OHLCV candlestick data with authenticity scoring baked in."""
    return await databus.fetch(
        "ohlcv", token=address, chain=chain, timeframe=timeframe, limit=limit
    )

@router.get("/premium/security-scan/{address}")
async def premium_security_scan(address: str, chain: str = "ethereum", request: Request = None) -> Any:
    """37+ security checks - GoPlus, honeypot, contract, liquidity, holders, rug pull indicators."""
    return await databus.fetch("token_security", address=address, chain=chain)

@router.get("/security-checks")
async def security_check_matrix() -> Any:
    """Full list of all 37+ security checks with weights and descriptions."""
    return await databus.fetch("token_security", action="matrix")

@router.get("/premium/smart-money")
async def smart_money_endpoint(chain: str = "solana", limit: int = 20) -> Any:
    """What profitable wallets are buying right now - with entity labels."""
    return await databus.fetch("smart_money", chain=chain, limit=limit)

@router.get("/premium/whale-alerts/{address}")
async def whale_alerts_endpoint(address: str, chain: str = "solana", min_value: float = 100000) -> Any:
    """Real-time large transaction detection for a token or wallet."""
    return await databus.fetch(
        "whale_alerts", address=address, chain=chain, min_value_usd=min_value
    )

@router.get("/premium/token-launches")
async def token_launches_endpoint(chain: str = "solana", limit: int = 50) -> Any:
    """Newly launched tokens with instant risk scoring by age."""
    return await databus.fetch("token_launches", chain=chain, limit=limit)

@router.get("/premium/insider-detection/{address}")
async def insider_detection_endpoint(address: str, chain: str = "solana") -> Any:
    """Detect pre-pump accumulation - volume spikes before major price moves."""
    return await databus.fetch("insider_detection", address=address, chain=chain)

@router.get("/premium/liquidity-risk/{address}")
async def liquidity_risk_endpoint(address: str, chain: str = "solana") -> Any:
    """LP health: concentration risk, lock status, holder entities via Arkham."""
    return await databus.fetch("liquidity_risk", address=address, chain=chain)

@router.get("/premium/holder-health/{address}")
async def holder_health_endpoint(address: str, chain: str = "solana") -> Any:
    """Holder distribution: Gini coefficient, top concentration, decentralization score."""
    return await databus.fetch("holder_health", address=address, chain=chain)

@router.get("/premium/cross-chain/{address}")
async def cross_chain_endpoint(address: str) -> Any:
    """Trace entity across all chains via Arkham. Discover all related addresses."""
    return await databus.fetch("cross_chain_entity", address=address)

@router.get("/premium/rug-patterns/{address}")
async def rug_patterns_endpoint(address: str, chain: str = "solana") -> Any:
    """Match token against 10 known rug pull patterns. Similarity scoring."""
    return await databus.fetch("rug_patterns", address=address, chain=chain)

@router.get("/premium/dev-reputation/{address}")
async def dev_reputation_endpoint(address: str, chain: str = "solana") -> Any:
    """Deployer wallet history: token count, lifespan, entity resolution."""
    return await databus.fetch("dev_reputation", address=address, chain=chain)

@router.get("/premium/token-report/{address}")
async def token_report_endpoint(address: str, chain: str = "solana") -> Any:
    """ONE-CALL enhanced token report. Smart verdicts, entity enrichment,
    trust adjustments, tier-aware. THE endpoint powering RugCharts."""
    return await databus.fetch("token_report", address=address, chain=chain)

@router.get("/tiers")
async def tier_comparison_endpoint() -> Any:
    """Competitive tier comparison: RugCharts vs DexScreener vs Nansen vs GMGN."""
    return await databus.fetch("tier_comparison")

