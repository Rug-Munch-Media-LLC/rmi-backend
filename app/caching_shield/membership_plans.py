"""
RMI Agent Membership Tools - High-value tool packages for AI agents.

Designed to attract agents to spend via x402 micropayments.
Each package solves a specific agent need with compelling value.
"""

import logging
from datetime import UTC, datetime

logger = logging.getLogger("agent_tools")

# ═══════════════════════════════════════════════════════════════════════════
# AGENT BUNDLES - Discounted tool packages for common agent workflows
# ═══════════════════════════════════════════════════════════════════════════

AGENT_BUNDLES = {
    "hunter_pack": {
        "name": "Token Hunter Pack",
        "description": "Everything an agent needs to find and vet new tokens before they pump. Fresh pair detection, sniper alerts, security scan, holder analysis, and deployer background check in one bundle.",
        "tools": [
            "fresh_pair",
            "sniper_alert",
            "rug_pull_predictor",
            "clone_detect",
            "deployer_history",
            "gmgn_security",
        ],
        "individual_price": 0.19,
        "bundle_price": 0.09,
        "discount": "53% off",
        "category": "alpha",
    },
    "whale_watcher": {
        "name": "Whale Watcher Suite",
        "description": "Track every move the big wallets make. Whale scanning, accumulation detection, smart money alerts, syndicate tracking, and wallet PnL analysis.",
        "tools": [
            "whale_scan",
            "whale_accumulation",
            "smart_money_alpha",
            "syndicate_scan",
            "wallet_pnl",
            "whale_profile",
        ],
        "individual_price": 0.28,
        "bundle_price": 0.14,
        "discount": "50% off",
        "category": "intelligence",
    },
    "forensic_pack": {
        "name": "Wallet Forensics Pack",
        "description": "Full investigation suite. Trace funding sources, map insider networks, detect wash trading, analyze wallet graphs, and run complete background checks.",
        "tools": [
            "funding_trace",
            "insider_network",
            "wash_trading",
            "wallet_graph",
            "deployer_history",
            "syndicate_track",
        ],
        "individual_price": 0.35,
        "bundle_price": 0.17,
        "discount": "51% off",
        "category": "security",
    },
    "market_pulse": {
        "name": "Market Pulse Pack",
        "description": "Real-time market intelligence. Price feeds, liquidity flow, arbitrage scanning, sentiment spikes, and trending detection across all chains.",
        "tools": [
            "pulse",
            "liquidity_flow",
            "arbitrage_scan",
            "sentiment_spike",
            "listing_predictor",
            "meme_vibe_score",
        ],
        "individual_price": 0.24,
        "bundle_price": 0.12,
        "discount": "50% off",
        "category": "market",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# SUBSCRIPTION TIERS - Recurring access for heavy agent usage
# ═══════════════════════════════════════════════════════════════════════════

SUBSCRIPTION_TIERS = {
    "scout": {
        "name": "Scout Tier",
        "price_monthly": 4.99,
        "daily_calls": 50,
        "tools": "All security and basic market tools",
        "discount_vs_paygo": "60%",
        "best_for": "Casual traders, hobby agents, Telegram bots",
    },
    "hunter": {
        "name": "Hunter Tier",
        "price_monthly": 14.99,
        "daily_calls": 200,
        "tools": "All tools including whale tracking and forensics",
        "discount_vs_paygo": "70%",
        "best_for": "Active traders, alpha groups, research agents",
    },
    "whale": {
        "name": "Whale Tier",
        "price_monthly": 49.99,
        "daily_calls": 1000,
        "tools": "Everything + priority support + webhook alerts",
        "discount_vs_paygo": "80%",
        "best_for": "Professional funds, market makers, trading bots",
    },
    "institution": {
        "name": "Institution Tier",
        "price_monthly": 199.99,
        "daily_calls": 5000,
        "tools": "Everything + dedicated RPC + custom models + SLA",
        "discount_vs_paygo": "90%",
        "best_for": "Funds, protocols, enterprises, high-frequency agents",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# REAL-TIME STREAMS - WebSocket subscriptions for live data
# ═══════════════════════════════════════════════════════════════════════════

STREAM_PRODUCTS = {
    "new_token_stream": {
        "name": "New Token Firehose",
        "description": "Real-time stream of every new token across Solana, Base, Ethereum, BSC. Filter by chain, liquidity threshold, launch platform. Delivered via WebSocket.",
        "price_hourly": 0.50,
        "price_daily": 9.99,
        "delivery": "WebSocket + webhook",
        "category": "streaming",
    },
    "whale_alert_stream": {
        "name": "Whale Alert Stream",
        "description": "Live alerts when whales move. Large transfers, exchange deposits/withdrawals, new position entries, accumulation patterns. Filter by wallet size and chain.",
        "price_hourly": 0.75,
        "price_daily": 14.99,
        "delivery": "WebSocket + webhook + Telegram",
        "category": "streaming",
    },
    "price_feed": {
        "name": "Multi-Chain Price Feed",
        "description": "Real-time price stream for any token across all supported DEXs. OHLCV candles, volume spikes, arbitrage opportunities. 1-second resolution.",
        "price_hourly": 0.30,
        "price_daily": 5.99,
        "delivery": "WebSocket",
        "category": "streaming",
    },
    "security_feed": {
        "name": "Security Alert Feed",
        "description": "Instant alerts for rug pulls, honeypots, exploits, flash loans, and suspicious token activity. Every alert includes detailed forensic data.",
        "price_hourly": 0.60,
        "price_daily": 11.99,
        "delivery": "WebSocket + webhook + Telegram",
        "category": "streaming",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# DEEP RESEARCH - Comprehensive investigation reports
# ═══════════════════════════════════════════════════════════════════════════

RESEARCH_PRODUCTS = {
    "token_deep_dive": {
        "name": "Token Deep Dive Report",
        "description": "Complete token investigation: contract audit, deployer background, holder analysis, liquidity history, social sentiment, risk scoring. Delivered as structured JSON + PDF summary.",
        "price": 0.75,
        "delivery_time": "30-60 seconds",
        "format": "JSON + PDF summary",
        "category": "research",
    },
    "wallet_profile": {
        "name": "Wallet Intelligence Profile",
        "description": "Full wallet dossier: PnL history, trading style classification, known associations, funding sources, scam involvement check, entity resolution.",
        "price": 0.50,
        "delivery_time": "15-30 seconds",
        "format": "JSON",
        "category": "research",
    },
    "chain_health": {
        "name": "Chain Health Report",
        "description": "Network-level analysis: gas trends, congestion metrics, MEV activity, validator health, TVL flows, upcoming governance events.",
        "price": 0.25,
        "delivery_time": "10-20 seconds",
        "format": "JSON",
        "category": "research",
    },
    "cross_chain_trace": {
        "name": "Cross-Chain Fund Trace",
        "description": "Trace funds across multiple chains. Follow the money through bridges, mixers, CEX deposits. Identify the final destination with confidence scoring.",
        "price": 1.50,
        "delivery_time": "60-120 seconds",
        "format": "JSON + graph visualization",
        "category": "research",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# BATCH PROCESSING - Bulk operations with volume discounts
# ═══════════════════════════════════════════════════════════════════════════

BATCH_PRODUCTS = {
    "batch_scan": {
        "name": "Batch Token Scanner",
        "description": "Scan up to 100 tokens simultaneously. Security checks, deployer analysis, holder distribution, liquidity assessment. Results in one structured response.",
        "max_tokens": 100,
        "price_per_10": 0.05,
        "discount": "75% vs individual scans",
        "category": "batch",
    },
    "batch_wallet": {
        "name": "Batch Wallet Analysis",
        "description": "Analyze up to 50 wallets at once. PnL, clustering, risk scores, whale overlap. Ideal for airdrop qualification and Sybil detection.",
        "max_wallets": 50,
        "price_per_10": 0.03,
        "discount": "80% vs individual",
        "category": "batch",
    },
    "batch_screen": {
        "name": "Batch Pre-Buy Screen",
        "description": "Quick rug check for up to 200 tokens. Binary safe/unsafe verdict with top risk factors. Under 5 seconds for full batch.",
        "max_tokens": 200,
        "price_per_50": 0.02,
        "discount": "90% vs individual",
        "category": "batch",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# AI-READY DATA FEEDS - Structured data optimized for LLM consumption
# ═══════════════════════════════════════════════════════════════════════════

AI_FEEDS = {
    "market_context": {
        "name": "AI Market Context Feed",
        "description": "LLM-optimized market summary. Top movers, trending narratives, fear/greed index, whale activity summary, upcoming events. Updated every 5 minutes.",
        "price_monthly": 9.99,
        "format": "Markdown + structured JSON",
        "category": "ai_feed",
    },
    "alpha_signals": {
        "name": "AI Alpha Signal Feed",
        "description": "Machine-ready trading signals. Smart money entries, accumulation patterns, insider buying, listing signals. Scored and ranked with confidence levels.",
        "price_monthly": 19.99,
        "format": "JSON with confidence scores",
        "category": "ai_feed",
    },
    "entity_graph": {
        "name": "Entity Relationship Graph",
        "description": "Pre-computed wallet clusters and entity mappings. Know which wallets are connected before you trade. Updated hourly.",
        "price_monthly": 14.99,
        "format": "JSON graph + adjacency list",
        "category": "ai_feed",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# AGENT SDK - Drop-in toolkit for AI agent developers
# ═══════════════════════════════════════════════════════════════════════════

AGENT_SDK_INFO = {
    "python": {
        "package": "rmi-agent-sdk",
        "install": "pip install rmi-agent-sdk",
        "description": "One-line integration for Python agents. Auto-handles x402 payments, caching, retries.",
    },
    "typescript": {
        "package": "@rugmunch/agent-sdk",
        "install": "npm install @rugmunch/agent-sdk",
        "description": "TypeScript SDK for Node.js agents. Full MCP client with built-in payment handling.",
    },
    "mcp_native": {
        "endpoint": "https:#mcp.rugmunch.io/mcp",
        "description": "Any MCP-compatible client connects directly. Payments handled via x402 protocol headers.",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# MEMBERSHIP CATALOG - Everything available for purchase
# ═══════════════════════════════════════════════════════════════════════════


def get_membership_catalog() -> dict:
    """Return the full membership catalog for agents."""
    return {
        "bundles": AGENT_BUNDLES,
        "subscriptions": SUBSCRIPTION_TIERS,
        "streams": STREAM_PRODUCTS,
        "research": RESEARCH_PRODUCTS,
        "batch": BATCH_PRODUCTS,
        "ai_feeds": AI_FEEDS,
        "agent_sdk": AGENT_SDK_INFO,
        "stats": {
            "total_bundles": len(AGENT_BUNDLES),
            "total_streams": len(STREAM_PRODUCTS),
            "total_research": len(RESEARCH_PRODUCTS),
            "total_batch": len(BATCH_PRODUCTS),
            "total_feeds": len(AI_FEEDS),
            "subscription_tiers": len(SUBSCRIPTION_TIERS),
            "updated_at": datetime.now(UTC).isoformat(),
        },
    }
