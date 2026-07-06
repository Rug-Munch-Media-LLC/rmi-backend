"""Rug Munch Intelligence - MCP Server.

97 tools for scam detection, rug pull prevention, and crypto intelligence across 7 chains.
The Bloomberg of Shitcoins Terminal Ultimate Edition.

Each tool sends POST to /api/v1/x402-tools/{tool_name} with chain + params.
x402 payment is handled transparently by the backend/gateway.
"""

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration - NO secrets, only public endpoints
# ---------------------------------------------------------------------------

API_BASE = os.environ.get("RUG_MUNCH_API_BASE", "https://mcp.rugmunch.io/api/v1")
API_KEY = os.environ.get("RUG_MUNCH_API_KEY", "")

SUPPORTED_CHAINS = ["base", "solana", "ethereum", "bsc", "arbitrum", "optimism", "polygon"]
DEFAULT_CHAIN = "solana"

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="Rug Munch Intelligence",
    instructions="97 tools for scam detection, rug pull prevention, and crypto intelligence across 7 chains.",
)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


async def _call_tool(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the x402-tools endpoint. Payment is handled by the gateway."""
    url = f"{API_BASE}/x402-tools/{tool_name}"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _chain_payload(chain: str, **kwargs: Any) -> dict[str, Any]:
    """Build a standard payload with chain + extra kwargs."""
    payload: dict[str, Any] = {"chain": chain or DEFAULT_CHAIN}
    payload.update({k: v for k, v in kwargs.items() if v is not None})
    return payload


# ===========================================================================
# INTELLIGENCE (28 tools)
# ===========================================================================


@mcp.tool()
async def whale_scan(chain: str = DEFAULT_CHAIN, address: str | None = None, min_usd: float | None = None) -> dict:
    """Track whale wallet movements and large transactions."""
    return await _call_tool("whale_scan", _chain_payload(chain, address=address, min_usd=min_usd))


@mcp.tool()
async def smartmoney(chain: str = DEFAULT_CHAIN, address: str | None = None, timeframe: str | None = None) -> dict:
    """Identify smart money wallets and their positions."""
    return await _call_tool("smartmoney", _chain_payload(chain, address=address, timeframe=timeframe))


@mcp.tool()
async def cluster(chain: str = DEFAULT_CHAIN, address: str | None = None, depth: int | None = None) -> dict:
    """Cluster related wallets by behavior patterns."""
    return await _call_tool("cluster", _chain_payload(chain, address=address, depth=depth))


@mcp.tool()
async def airdrop_finder(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Find eligible airdrops for a wallet."""
    return await _call_tool("airdrop_finder", _chain_payload(chain, address=address))


@mcp.tool()
async def whale_profile(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Deep profile of a whale wallet's history."""
    return await _call_tool("whale_profile", _chain_payload(chain, address=address))


@mcp.tool()
async def insider(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Detect insider trading patterns before launches."""
    return await _call_tool("insider", _chain_payload(chain, token=token))


@mcp.tool()
async def smart_money_alpha(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Alpha signals from smart money activity."""
    return await _call_tool("smart_money_alpha", _chain_payload(chain, token=token))


@mcp.tool()
async def social_signal(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Social media signal aggregation for tokens."""
    return await _call_tool("social_signal", _chain_payload(chain, token=token))


@mcp.tool()
async def sentiment(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Market sentiment analysis across sources."""
    return await _call_tool("sentiment", _chain_payload(chain, token=token))


@mcp.tool()
async def copy_trade_finder(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Find wallets worth copy-trading."""
    return await _call_tool("copy_trade_finder", _chain_payload(chain, address=address))


@mcp.tool()
async def liquidity_flow(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Track liquidity inflows and outflows."""
    return await _call_tool("liquidity_flow", _chain_payload(chain, token=token))


@mcp.tool()
async def risk_monitor(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Continuous risk monitoring for positions."""
    return await _call_tool("risk_monitor", _chain_payload(chain, token=token))


@mcp.tool()
async def rug_pull_predictor(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """ML-based rug pull prediction scoring."""
    return await _call_tool("rug_pull_predictor", _chain_payload(chain, token=token))


@mcp.tool()
async def meme_vibe_score(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Meme coin community vibe and virality scoring."""
    return await _call_tool("meme_vibe_score", _chain_payload(chain, token=token))


@mcp.tool()
async def nft_wash_detector(chain: str = DEFAULT_CHAIN, collection: str | None = None) -> dict:
    """Detect NFT wash trading patterns."""
    return await _call_tool("nft_wash_detector", _chain_payload(chain, collection=collection))


@mcp.tool()
async def gas_forecast(chain: str = DEFAULT_CHAIN) -> dict:
    """Gas price forecasting and optimization."""
    return await _call_tool("gas_forecast", _chain_payload(chain))


@mcp.tool()
async def bridge_security(chain: str = DEFAULT_CHAIN, bridge: str | None = None) -> dict:
    """Cross-chain bridge risk assessment."""
    return await _call_tool("bridge_security", _chain_payload(chain, bridge=bridge))


@mcp.tool()
async def defi_yield_scanner(chain: str = DEFAULT_CHAIN, protocol: str | None = None) -> dict:
    """Scan DeFi yields and risk-adjusted returns."""
    return await _call_tool("defi_yield_scanner", _chain_payload(chain, protocol=protocol))


@mcp.tool()
async def portfolio_tracker(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Track and analyze portfolio composition."""
    return await _call_tool("portfolio_tracker", _chain_payload(chain, address=address))


@mcp.tool()
async def token_deep_dive(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Deep fundamental analysis of any token."""
    return await _call_tool("token_deep_dive", _chain_payload(chain, token=token))


@mcp.tool()
async def token_comparison(chain: str = DEFAULT_CHAIN, token_a: str | None = None, token_b: str | None = None) -> dict:
    """Side-by-side token comparison analysis."""
    return await _call_tool("token_comparison", _chain_payload(chain, token_a=token_a, token_b=token_b))


@mcp.tool()
async def forensic_valuation(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Forensic-based token valuation modeling."""
    return await _call_tool("forensic_valuation", _chain_payload(chain, token=token))


@mcp.tool()
async def comprehensive_audit(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Full-scope audit of a token or contract."""
    return await _call_tool("comprehensive_audit", _chain_payload(chain, token=token))


@mcp.tool()
async def investigation_report(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Generate detailed investigation reports."""
    return await _call_tool("investigation_report", _chain_payload(chain, token=token))


@mcp.tool()
async def market_overview(chain: str = DEFAULT_CHAIN) -> dict:
    """Real-time market overview and metrics."""
    return await _call_tool("market_overview", _chain_payload(chain))


@mcp.tool()
async def chain_health(chain: str = DEFAULT_CHAIN) -> dict:
    """Blockchain network health and congestion metrics."""
    return await _call_tool("chain_health", _chain_payload(chain))


@mcp.tool()
async def anomaly(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Detect anomalous on-chain behavior."""
    return await _call_tool("anomaly", _chain_payload(chain, token=token))


@mcp.tool()
async def honeypot_check(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Check if a token is a honeypot scam."""
    return await _call_tool("honeypot_check", _chain_payload(chain, token=token))


@mcp.tool()
async def mev_protection(chain: str = DEFAULT_CHAIN) -> dict:
    """Assess MEV exposure and protection strategies."""
    return await _call_tool("mev_protection", _chain_payload(chain))


# ===========================================================================
# SECURITY (17 tools) - some shared names, different tool registration context
# ===========================================================================


@mcp.tool()
async def urlcheck(url: str | None = None) -> dict:
    """Check URLs for phishing and scam indicators."""
    return await _call_tool("urlcheck", {"url": url} if url else {})


@mcp.tool()
async def rugshield(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Real-time rug pull shield and protection."""
    return await _call_tool("rugshield", _chain_payload(chain, token=token))


@mcp.tool()
async def clone_detect(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Detect cloned/fake tokens and contracts."""
    return await _call_tool("clone_detect", _chain_payload(chain, token=token))


@mcp.tool()
async def fresh_pair(chain: str = DEFAULT_CHAIN, pair: str | None = None) -> dict:
    """Analyze freshly launched trading pairs."""
    return await _call_tool("fresh_pair", _chain_payload(chain, pair=pair))


@mcp.tool()
async def profile_flip(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Profile flip risk for token distributions."""
    return await _call_tool("profile_flip", _chain_payload(chain, token=token))


@mcp.tool()
async def audit(chain: str = DEFAULT_CHAIN, contract: str | None = None) -> dict:
    """Smart contract security audit."""
    return await _call_tool("audit", _chain_payload(chain, contract=contract))


# ===========================================================================
# MARKET (14 tools)
# ===========================================================================


@mcp.tool()
async def pulse(chain: str = DEFAULT_CHAIN) -> dict:
    """Real-time market pulse and momentum."""
    return await _call_tool("pulse", _chain_payload(chain))


@mcp.tool()
async def coingecko_global(chain: str = DEFAULT_CHAIN) -> dict:
    """Global market data from CoinGecko."""
    return await _call_tool("coingecko_global", _chain_payload(chain))


@mcp.tool()
async def coingecko_trending(chain: str = DEFAULT_CHAIN) -> dict:
    """Trending tokens on CoinGecko."""
    return await _call_tool("coingecko_trending", _chain_payload(chain))


@mcp.tool()
async def coingecko_markets(chain: str = DEFAULT_CHAIN, category: str | None = None) -> dict:
    """Market cap and volume data."""
    return await _call_tool("coingecko_markets", _chain_payload(chain, category=category))


@mcp.tool()
async def coingecko_categories(chain: str = DEFAULT_CHAIN) -> dict:
    """CoinGecko category analysis."""
    return await _call_tool("coingecko_categories", _chain_payload(chain))


# ===========================================================================
# ANALYSIS (12 tools)
# ===========================================================================


@mcp.tool()
async def tx_decoder(chain: str = DEFAULT_CHAIN, tx_hash: str | None = None) -> dict:
    """Decode and interpret transactions."""
    return await _call_tool("tx_decoder", _chain_payload(chain, tx_hash=tx_hash))


@mcp.tool()
async def wallet(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Comprehensive wallet analysis."""
    return await _call_tool("wallet", _chain_payload(chain, address=address))


@mcp.tool()
async def contract_info(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Extract smart contract information."""
    return await _call_tool("contract_info", _chain_payload(chain, address=address))


@mcp.tool()
async def storage_reader(chain: str = DEFAULT_CHAIN, contract: str | None = None, slot: str | None = None) -> dict:
    """Read contract storage slots."""
    return await _call_tool("storage_reader", _chain_payload(chain, contract=contract, slot=slot))


# ===========================================================================
# FORENSICS (7 tools)
# ===========================================================================


@mcp.tool()
async def forensics_threat_check(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Threat intelligence check for addresses."""
    return await _call_tool("forensics_threat_check", _chain_payload(chain, address=address))


@mcp.tool()
async def forensics_risk_report(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Detailed forensics risk report."""
    return await _call_tool("forensics_risk_report", _chain_payload(chain, address=address))


@mcp.tool()
async def forensics_deep_scan(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Deep forensic scan of wallet/activity."""
    return await _call_tool("forensics_deep_scan", _chain_payload(chain, address=address))


@mcp.tool()
async def forensics_cross_chain(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Cross-chain forensic correlation."""
    return await _call_tool("forensics_cross_chain", _chain_payload(chain, address=address))


@mcp.tool()
async def bundle_security_pack(chain: str = DEFAULT_CHAIN, token: str | None = None) -> dict:
    """Bundle of security forensics tools."""
    return await _call_tool("bundle_security_pack", _chain_payload(chain, token=token))


@mcp.tool()
async def forensics_trace(chain: str = DEFAULT_CHAIN, address: str | None = None, depth: int | None = None) -> dict:
    """Trace funds through complex transaction graphs."""
    return await _call_tool("forensics_trace", _chain_payload(chain, address=address, depth=depth))


@mcp.tool()
async def forensics_entity(chain: str = DEFAULT_CHAIN, address: str | None = None) -> dict:
    """Entity resolution from on-chain footprints."""
    return await _call_tool("forensics_entity", _chain_payload(chain, address=address))


# ===========================================================================
# SOCIAL (7 tools)
# ===========================================================================


@mcp.tool()
async def tw_profile(username: str | None = None) -> dict:
    """Twitter/X profile analysis."""
    payload = {"chain": DEFAULT_CHAIN}
    if username:
        payload["username"] = username
    return await _call_tool("tw_profile", payload)


@mcp.tool()
async def tw_timeline(username: str | None = None, limit: int | None = None) -> dict:
    """Twitter/X timeline analysis."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if username:
        payload["username"] = username
    if limit:
        payload["limit"] = limit
    return await _call_tool("tw_timeline", payload)


@mcp.tool()
async def tw_search(query: str | None = None) -> dict:
    """Twitter/X search for crypto mentions."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if query:
        payload["query"] = query
    return await _call_tool("tw_search", payload)


@mcp.tool()
async def profile_get(address: str | None = None) -> dict:
    """Get Rug Munch user profile."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if address:
        payload["address"] = address
    return await _call_tool("profile_get", payload)


@mcp.tool()
async def profile_badges(address: str | None = None) -> dict:
    """Get user badges and reputation."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if address:
        payload["address"] = address
    return await _call_tool("profile_badges", payload)


# ===========================================================================
# OSINT (3 tools)
# ===========================================================================


@mcp.tool()
async def osint_search(query: str | None = None) -> dict:
    """Open source intelligence search."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if query:
        payload["query"] = query
    return await _call_tool("osint_search", payload)


@mcp.tool()
async def osint_identity_hunt(identifier: str | None = None) -> dict:
    """Cross-reference identities across platforms."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if identifier:
        payload["identifier"] = identifier
    return await _call_tool("osint_identity_hunt", payload)


@mcp.tool()
async def osint_investigate(target: str | None = None) -> dict:
    """Deep OSINT investigation."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if target:
        payload["target"] = target
    return await _call_tool("osint_investigate", payload)


# ===========================================================================
# LAUNCHPAD (3 tools)
# ===========================================================================


@mcp.tool()
async def launch(chain: str = DEFAULT_CHAIN) -> dict:
    """Track token launches in real-time."""
    return await _call_tool("launch", _chain_payload(chain))


@mcp.tool()
async def launch_intel(chain: str = DEFAULT_CHAIN) -> dict:
    """Intelligence on upcoming launches."""
    return await _call_tool("launch_intel", _chain_payload(chain))


@mcp.tool()
async def sniper_alert(chain: str = DEFAULT_CHAIN) -> dict:
    """Snipe alerts for high-potential launches."""
    return await _call_tool("sniper_alert", _chain_payload(chain))


# ===========================================================================
# META (3 tools)
# ===========================================================================


@mcp.tool()
async def list_bundles() -> dict:
    """List available tool bundles."""
    return await _call_tool("list_bundles", {"chain": DEFAULT_CHAIN})


@mcp.tool()
async def tools_discovery() -> dict:
    """Discover all available tools."""
    return await _call_tool("tools_discovery", {"chain": DEFAULT_CHAIN})


@mcp.tool()
async def framework_discovery() -> dict:
    """Discover framework capabilities."""
    return await _call_tool("framework_discovery", {"chain": DEFAULT_CHAIN})


# ===========================================================================
# NEWS (3 tools)
# ===========================================================================


@mcp.tool()
async def news_headlines(category: str | None = None) -> dict:
    """Latest crypto news headlines."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if category:
        payload["category"] = category
    return await _call_tool("news_headlines", payload)


@mcp.tool()
async def news_twitter(query: str | None = None) -> dict:
    """Crypto news from Twitter/X."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if query:
        payload["query"] = query
    return await _call_tool("news_twitter", payload)


@mcp.tool()
async def news_combined(category: str | None = None) -> dict:
    """Combined news from all sources."""
    payload: dict[str, Any] = {"chain": DEFAULT_CHAIN}
    if category:
        payload["category"] = category
    return await _call_tool("news_combined", payload)


# ===========================================================================
# Entry point
# ===========================================================================


def main():
    """Run the Rug Munch Intelligence MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
