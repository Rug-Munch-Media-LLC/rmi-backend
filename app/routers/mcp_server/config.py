"""MCP Server config — constants and helpers."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("rmi_mcp_v3")

MCP_PROTOCOL_VERSION = "2026-07-28"
SERVER_NAME = "Rug Munch Intelligence"
SERVER_VERSION = "3.2.0"


def _get_tools() -> dict[str, Any]:
    try:
        from app.routers.x402_enforcement import CHAIN_USDC, TOOL_PRICES

        return {"prices": dict(TOOL_PRICES), "chains": dict(CHAIN_USDC)}
    except ImportError:
        return {"prices": {}, "chains": {}}


def _get_facilitator_count() -> int:
    try:
        from app.facilitators.base import get_registry

        return len(get_registry().get_all())
    except ImportError:
        return 10


def _desc(tool_id: str, pricing: dict[str, Any]) -> str:
    d = pricing.get("description", "")
    if d and d != tool_id and len(d) > 10:
        return d
    fallbacks = {
        "airdrop_check": "Verify airdrop legitimacy -- contract audit, distribution analysis, scam pattern detection.",
        "airdrop_finder": "Discover active and upcoming airdrops across all major chains.",
        "all_in_one": "All-in-One Audit -- comprehensive security scan.",
        "alpha_digest": "Alpha digest -- curated crypto alpha from top-performing wallets.",
        "arbitrage_scan": "Cross-chain and cross-DEX arbitrage scanner.",
        "bundler_detect": "MEV bundler detector -- sandwich attacks, frontrunning, backrunning patterns.",
        "catalog": "Full tool catalog -- every RMI tool with pricing, chain support, trial availability.",
        "clone_detect": "Clone contract detector -- bytecode similarity analysis, function matching.",
        "deployer_history": "Deployer history investigation -- every token this wallet has launched.",
        "fresh_pair": "Fresh pair scanner -- detect newly created trading pairs.",
        "insider_network": "Insider network mapper -- trace connected wallets.",
        "intelligence_pack": "Intelligence Pack -- whale tracking + smart money + wallet clustering at 29% discount.",
        "kol_performance": "KOL performance tracker -- measure influencer call accuracy.",
        "liquidity_depth": "Liquidity depth analyzer -- order book depth, slippage estimation.",
        "liquidity_flow": "Liquidity flow tracker -- track where capital is moving across chains.",
        "liquidity_migration": "Liquidity migration detector -- tokens moving pools.",
        "listing_predictor": "Exchange listing predictor -- on-chain signals suggesting imminent listing.",
        "meme_vibe_score": "Meme coin vibe score -- social virality, holder growth rate.",
        "mev_alert": "MEV alert system -- real-time sandwich attack detection.",
        "mev_protection": "MEV protection checker -- verify if your transaction is protected.",
        "portfolio_aggregate": "Portfolio aggregator -- combine multiple wallets into a single dashboard.",
        "profile_flip": "Profile flip detector -- sudden Twitter/X profile changes.",
        "protocol_risk": "Protocol risk assessment -- TVL stability, admin key analysis.",
        "rug_pull_predictor": "Rug pull predictor -- AI-powered risk scoring using 12+ signals.",
        "scam_database": "Scam database lookup -- check addresses against known scam databases.",
        "security_pack": "Security Pack -- honeypot + rug pull + audit + clone detection at 23% discount.",
        "sentiment_spike": "Sentiment spike detector -- real-time social media volume anomalies.",
        "smart_money_alpha": "Smart money alpha -- real-time alerts when top-performing wallets enter new positions.",
        "sniper_alert": "Sniper alert system -- detect sniper bots entering new token launches.",
        "syndicate_scan": "Syndicate scanner -- identify coordinated trading groups.",
        "syndicate_track": "Syndicate tracker -- follow known syndicate wallets.",
        "token_age": "Token age verifier -- contract creation date, migration history.",
        "unlock_calendar": "Token unlock calendar -- track vesting schedules.",
        "wallet_graph": "Wallet graph analysis -- visualize transaction flows.",
        "wallet_pnl": "Wallet PnL calculator -- realized/unrealized gains, win rate, ROI.",
        "wash_trading": "Wash trading detector -- identify fake volume, self-trades.",
        "whale_accumulation": "Whale accumulation detector -- track large wallet accumulation.",
        "whale_profile": "Whale profile -- complete analysis of large holder wallets.",
        "whale_scan": "Whale scanner -- real-time whale activity across chains.",
        "anomaly": "Anomaly detector -- flag unusual transaction patterns.",
        "audit": "Smart contract audit -- static analysis, vulnerability detection.",
        "bridge_security": "Cross-chain bridge security audit -- liquidity verification.",
        "chain_health": "Chain health monitor -- network congestion, gas trends.",
        "cluster": "Wallet cluster analysis -- identify linked addresses.",
        "comprehensive_audit": "Comprehensive audit -- full security scan combining all checks.",
        "copy_trade_finder": "Copy trade discovery -- find wallets with proven track records.",
        "defi_yield_scanner": "DeFi yield scanner -- scan across chains for highest APY.",
        "forensics": "On-chain forensics -- deep transaction tracing and money flow analysis.",
        "gas_forecast": "Gas price forecast -- predict optimal transaction timing.",
        "honeypot_check": "Honeypot detector -- verify if a token contract prevents selling.",
        "insider": "Insider activity tracker -- detect pre-launch accumulation.",
        "launch": "Token launch analyzer -- new token launch evaluation.",
        "launch_intel": "Launch intelligence -- comprehensive new token assessment.",
        "market_overview": "Market overview -- aggregate crypto market data.",
        "nft_wash_detector": "NFT wash trading detector -- identify self-bought NFTs.",
        "portfolio_tracker": "Portfolio tracker -- monitor wallet holdings over time.",
        "pulse": "Market pulse -- real-time snapshot of market sentiment.",
        "risk_monitor": "Risk monitor -- continuous risk assessment for watched tokens.",
        "rugshield": "RugShield instant check -- rapid safety assessment.",
        "sentiment": "Crypto sentiment analysis -- aggregate social sentiment.",
        "smartmoney": "Smart money tracker -- follow wallets with consistently high ROI.",
        "sniper_detect": "Sniper bot detector -- identify automated sniping wallets.",
        "social_signal": "Social signal aggregator -- combine Twitter, Reddit, Telegram buzz.",
        "token_comparison": "Token comparison -- side-by-side analysis of two or more tokens.",
        "token_deep_dive": "Token deep dive -- exhaustive single-token analysis.",
        "token_watch_alerts": "Token watch alerts -- push notifications for watched tokens.",
        "token_watch_list": "Token watch list -- view all your monitored tokens.",
        "tw_profile": "X/Twitter profile analysis -- crypto account credibility.",
        "tw_search": "X/Twitter crypto search -- find tweets about any token.",
        "tw_timeline": "X/Twitter timeline feed -- curated crypto timeline.",
        "urlcheck": "URL security checker -- scan crypto URLs for phishing patterns.",
        "wallet": "Wallet analysis -- comprehensive wallet assessment.",
        "whale": "Whale tracker -- monitor large holder activity.",
    }
    return fallbacks.get(
        tool_id,
        f"{tool_id.replace('_', ' ').title()} -- real-time crypto intelligence and security analysis.",
    )


def _input_schema(tool_id: str) -> dict[str, Any]:
    """Return MCP-compliant inputSchema for a tool."""
    base_address = {
        "type": "object",
        "properties": {
            "address": {
                "type": "string",
                "description": "Wallet address, token contract, or ENS name to analyze",
            },
            "chain": {
                "type": "string",
                "description": "Blockchain to query (base, ethereum, solana, bsc, polygon, arbitrum, optimism, avalanche, fantom, gnosis, tron, bitcoin)",
                "enum": [
                    "base",
                    "ethereum",
                    "solana",
                    "bsc",
                    "polygon",
                    "arbitrum",
                    "optimism",
                    "avalanche",
                    "fantom",
                    "gnosis",
                    "tron",
                    "bitcoin",
                ],
            },
        },
        "required": ["address"],
    }
    base_url = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL or contract address to analyze"},
            "chain": {"type": "string", "description": "Blockchain"},
        },
        "required": ["url"],
    }
    base_none = {"type": "object", "properties": {}, "required": []}

    schemas = {
        "urlcheck": base_url,
        "honeypot_check": base_address,
        "rug_pull_check": base_address,
        "contract_audit": base_address,
        "whale_scan": {**base_address, "required": []},
        "whale_profile": base_address,
        "whale_accumulation": base_address,
        "wallet": base_address,
        "token_analysis": base_address,
        "degen_scan": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Token contract address"},
                "chain": {"type": "string", "description": "Blockchain"},
            },
            "required": ["address"],
        },
        "smart_money_alpha": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address"},
                "chain": {"type": "string", "description": "Blockchain"},
                "limit": {"type": "integer", "description": "Number of results"},
            },
        },
        "sentiment_spike": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Token name or address"},
                "chain": {"type": "string", "description": "Blockchain"},
            },
        },
        "catalog": base_none,
    }
    return schemas.get(tool_id, base_address)
