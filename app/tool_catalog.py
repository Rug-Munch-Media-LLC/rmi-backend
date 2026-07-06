"""
RMI Tool Catalog - Organized for Humans & Bots
===============================================
Structured catalog of all 170 crypto security and intelligence tools.
Organized by category with SEO-optimized descriptions, keywords, and use cases.
Serves both human-readable discovery and bot-parseable structured data.

Categories:
    SECURITY (38)     - Scam detection, contract audit, threat prevention, SENTINEL deep scan
    INTELLIGENCE (27)  - Wallet profiling, whale tracking, pattern detection
    MARKET (15)        - Price data, liquidity analysis, market health
    ANALYSIS (14)      - Portfolio, forensics, deep token investigation
    SOCIAL (11)        - Sentiment, Twitter/X intelligence, signal detection
    LAUNCHPAD (7)      - New token discovery, launch intelligence
    PREMIUM (7)        - Advanced investigation packs
    DEFI (4)           - Yield scanning, aggregator, impermanent loss
    NFT (2)            - Wash trading, floor analytics
    MCP EXTERNAL (150+) - Third-party data provider tools
"""

from typing import Any

# ── Tool Categories with SEO metadata ──────────────────────────

TOOL_CATEGORIES = {
    "security": {
        "name": "Security & Scam Detection",
        "slug": "security",
        "description": "Real-time crypto scam detection, contract audits, honeypot checks, and threat prevention. Protect your assets with AI-powered security scanning across all major blockchains.",
        "keywords": [
            "crypto scam detection",
            "smart contract audit",
            "honeypot checker",
            "rug pull detector",
            "token security scan",
            "blockchain security",
            "MEV protection",
            "wash trading detection",
        ],
        "icon": "🛡️",
        "priority": 1,
    },
    "intelligence": {
        "name": "Wallet Intelligence & Tracking",
        "slug": "intelligence",
        "description": "Track whale wallets, detect insider trading patterns, analyze smart money flows, and map syndicate networks. Advanced blockchain intelligence for traders and investigators.",
        "keywords": [
            "whale tracker",
            "wallet intelligence",
            "smart money tracking",
            "insider trading detection",
            "syndicate analysis",
            "blockchain forensics",
            "crypto wallet profiling",
        ],
        "icon": "🔍",
        "priority": 2,
    },
    "market": {
        "name": "Market Analysis & Data",
        "slug": "market",
        "description": "Real-time market data, token pricing, liquidity depth analysis, arbitrage scanning, and chain health monitoring. Comprehensive crypto market intelligence.",
        "keywords": [
            "crypto market data",
            "token price",
            "liquidity analysis",
            "arbitrage scanner",
            "market pulse",
            "chain health",
            "crypto trading signals",
            "DEX analytics",
        ],
        "icon": "📊",
        "priority": 3,
    },
    "analysis": {
        "name": "Portfolio & Asset Analysis",
        "slug": "analysis",
        "description": "Portfolio tracking, P&L calculation, token comparison, forensic investigation, and deep token due diligence. Professional-grade crypto analysis tools.",
        "keywords": [
            "crypto portfolio tracker",
            "token analysis",
            "forensic investigation",
            "wallet P&L",
            "NFT wash trading",
            "token comparison",
            "crypto due diligence",
        ],
        "icon": "📈",
        "priority": 4,
    },
    "social": {
        "name": "Social & Sentiment Intelligence",
        "slug": "social",
        "description": "Twitter/X profile analysis, sentiment tracking, social signal detection, and influencer performance metrics. Understand what crypto Twitter is saying before the market moves.",
        "keywords": [
            "crypto sentiment analysis",
            "Twitter crypto tracker",
            "social signal detection",
            "KOL performance",
            "crypto social media intelligence",
            "sentiment spike detector",
        ],
        "icon": "🐦",
        "priority": 5,
    },
    "launchpad": {
        "name": "Launch & Token Discovery",
        "slug": "launch",
        "description": "New token discovery, launch intelligence, sniper alerts, and airdrop tracking. Find the next big thing before it launches.",
        "keywords": [
            "new crypto tokens",
            "token launch tracker",
            "sniper bot alert",
            "airdrop finder",
            "early token discovery",
            "crypto launch intelligence",
        ],
        "icon": "🚀",
        "priority": 6,
    },
    "premium": {
        "name": "Premium Investigation Suite",
        "slug": "premium",
        "description": "Advanced forensic investigation, OSINT identity hunting, institutional-grade token valuation, and comprehensive investigation reports. Professional-grade tools for serious investigators.",
        "keywords": [
            "crypto forensic investigation",
            "OSINT crypto",
            "token valuation DCF",
            "scam investigation report",
            "institutional crypto analysis",
            "professional blockchain investigation",
        ],
        "icon": "💎",
        "priority": 7,
    },
    "mcp-external": {
        "name": "Data Provider Network",
        "slug": "data",
        "description": "150+ tools from 28 data providers including DexScreener, Jupiter, Helius, Birdeye, CoinGecko, Nansen, Arkham, GMGN, Moralis, PumpFun, DeFiLlama and more. Direct API access to the best crypto data sources.",
        "keywords": [
            "DexScreener API",
            "Jupiter DEX",
            "Helius Solana",
            "Birdeye token data",
            "CoinGecko API",
            "Nansen intelligence",
            "Arkham data",
            "crypto data providers",
        ],
        "icon": "🔌",
        "priority": 8,
    },
}


# ── Human-readable tool guide ──────────────────────────────────


def get_human_catalog() -> dict[str, Any]:
    """Build a human-friendly tool catalog organized by use case and tier.

    Returns structured data suitable for frontend display - categories,
    tools, descriptions, pricing tiers, and SEO metadata.
    """
    from app.routers.x402_databus_tools import X402_TOOL_PRICING as DATABUS_TOOLS
    from app.routers.x402_enforcement import TOOL_PRICES
    from app.tool_tiers import TIER_PRICING, TOOL_TIERS

    catalog = {
        "meta": {
            "name": "Rug Munch Intelligence - Crypto Security & Analysis Tools",
            "description": "170 crypto security, intelligence, and market analysis tools accessible via API, MCP, and web interface. AI-powered scam detection, whale tracking, contract auditing, and forensics across 13 blockchains. Basic, Premium, and Elite tiers for every need.",
            "seo_keywords": [
                "crypto scam detection",
                "rug pull checker",
                "honeypot detector",
                "smart contract audit",
                "whale tracker",
                "wallet intelligence",
                "blockchain forensics",
                "crypto security tools",
                "token analysis",
                "DeFi security",
                "NFT scam detection",
                "MEV protection",
                "crypto market data",
                "DEX analytics",
                "crypto trading tools",
            ],
            "total_tools": len(TOOL_PRICES) + len([k for k in DATABUS_TOOLS if k not in TOOL_PRICES]),
            "total_categories": len(TOOL_CATEGORIES),
            "chains_supported": 13,
            "facilitators": 10,
            "free_trials": "1-5 free calls per tool",
            "pricing": "$0.01 - $0.40 per call",
            "tiers": TIER_PRICING,
        },
        "tiers": TIER_PRICING,
        "categories": {},
    }

    for cat_key, cat_info in TOOL_CATEGORIES.items():
        cat_tools = []

        if cat_key == "mcp-external":
            cat_tools.append(
                {
                    "id": "mcp-external",
                    "name": "External Data Provider Tools (150+)",
                    "description": cat_info["description"],
                    "tier": "basic",
                    "providers": [
                        "DexScreener",
                        "Jupiter",
                        "Helius",
                        "Birdeye",
                        "CoinGecko",
                        "Nansen",
                        "Arkham",
                        "GMGN",
                        "Moralis",
                        "PumpFun",
                        "Raydium",
                        "DeFiLlama",
                        "DexPaprika",
                        "CoinCap",
                        "CoinMarketCap",
                        "CryptoPanic",
                        "CryptoCompare",
                        "Blockchair",
                        "Blockchain.com",
                        "Mempool",
                        "Solana RPC",
                        "CryptoIZ",
                        "Blockrun",
                        "AgentFi",
                        "Solscan",
                        "QuickNode",
                        "FreeUSDC",
                        "Dune Analytics",
                    ],
                    "how_to_access": "Available via MCP tools/list on sol.rugmunch.io/mcp and base.rugmunch.io/mcp",
                }
            )
        else:
            for tool_id, pricing in TOOL_PRICES.items():
                if pricing.get("category") == cat_key:
                    tier_info = TOOL_TIERS.get(tool_id, {})
                    cat_tools.append(
                        {
                            "id": tool_id,
                            "name": tier_info.get("name", tool_id.replace("_", " ").title()),
                            "description": tier_info.get("description", pricing.get("description", "")),
                            "price_usd": tier_info.get("price_usd", pricing.get("price_usd", 0.01)),
                            "trial_free": tier_info.get("trial_free", pricing.get("trial_free", 1)),
                            "tier": tier_info.get("tier", pricing.get("category", "basic")),
                            "category": cat_key,
                        }
                    )
            # Add databus-only tools to matching categories
            for tool_id, pricing in DATABUS_TOOLS.items():
                if tool_id not in TOOL_PRICES and pricing.get("category", "data") == cat_key:
                    cat_tools.append(
                        {
                            "id": tool_id,
                            "name": tool_id.replace("_", " ").title(),
                            "description": pricing.get(
                                "description",
                                f"{tool_id.replace('_', ' ').title()} -- DataBus-powered crypto intelligence.",
                            ),
                            "price_usd": pricing.get("price_usd", 0.05),
                            "trial_free": pricing.get("trial_free", 1),
                            "tier": pricing.get("tier", "basic"),
                            "category": cat_key,
                        }
                    )

        if cat_tools or cat_key == "mcp-external":
            catalog["categories"][cat_key] = {
                **cat_info,
                "tool_count": len(cat_tools),
                "tools": sorted(cat_tools, key=lambda t: t.get("name", "")),
            }

    return catalog


def get_bot_catalog() -> dict[str, Any]:
    """Build a bot-optimized tool catalog with minimal descriptions.

    Returns flat tool list with IDs, parameters, and chain support
    for AI agents to quickly discover and call tools.
    """
    from app.routers.x402_databus_tools import X402_TOOL_PRICING as DATABUS_TOOLS
    from app.routers.x402_enforcement import TOOL_PRICES

    # Merge enforcement + databus-only tools = single source of truth
    all_tools = dict(TOOL_PRICES)
    for tid, pricing in DATABUS_TOOLS.items():
        if tid not in all_tools:
            all_tools[tid] = pricing

    tools = []
    for tool_id, pricing in all_tools.items():
        tools.append(
            {
                "id": tool_id,
                "name": pricing.get("description", tool_id),
                "price_usd": pricing.get("price_usd", 0.01),
                "price_atoms": pricing.get("price_atoms", "10000"),
                "category": pricing.get("category", "analysis"),
                "trial_free": pricing.get("trial_free", 1),
                "chains": _get_chains_for_tool(tool_id),
            }
        )

    return {
        "protocol": "x402",
        "version": "2",
        "endpoint": "https://mcp.rugmunch.io/api/v1/x402-tools/{tool_id}",
        "discovery": "https://mcp.rugmunch.io/.well-known/x402",
        "total_tools": len(tools),
        "categories": {
            cat: sum(1 for t in tools if t["category"] == cat) for cat in sorted({t["category"] for t in tools})
        },
        "chains_supported": 13,
        "tools": sorted(tools, key=lambda t: t["category"]),
    }


def _get_chains_for_tool(tool_id: str) -> list[str]:
    """Get supported chains for a tool."""
    # Most RMI tools support all chains
    solana_only = {
        "launch_intel",
        "whale_profile",
        "sniper_detect",
        "sniper_alert",
        "bundler_detect",
        "liquidity_migration",
        "profile_flip",
        "fresh_pair",
        "clone_detect",
        "deployer_history",
    }
    base_only = set()

    if tool_id in solana_only:
        return ["solana"]
    if tool_id in base_only:
        return ["base"]
    return ["base", "solana", "ethereum", "bsc", "polygon", "arbitrum"]
