"""
Canonical Tool Prices — Basic, Market, API, Monitoring, and Bundle categories.
"""

MARKET_BASIC_TOOLS = {
    # ── basic ──
    "catalog": {"price_usd": 0.0, "price_atoms": "0", "category": "api", "trial_free": 999, "description": "Browse available tools, pricing, and chain support"},
    "defi_protocols": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 5, "description": "DeFi protocol tracker - TVL, chains, categories, revenue metrics"},
    "dex_data": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 5, "description": "DEX pool data - liquidity depth, volume, price impact for any token"},
    "market_movers": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 5, "description": "Top gainers, losers, and volume movers across all chains"},
    "news": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 5, "description": "Crypto news feed - aggregated headlines, filtered by topic"},
    "prediction_markets": {"price_usd": 0.02, "price_atoms": "20000", "category": "basic", "trial_free": 3, "description": "Prediction market odds - event probabilities and trading volumes"},
    "prediction_signals": {"price_usd": 0.02, "price_atoms": "20000", "category": "basic", "trial_free": 3, "description": "Trading signals - sentiment, momentum, and contrarian indicators"},
    "risk_scan": {"price_usd": 0.02, "price_atoms": "20000", "category": "basic", "trial_free": 3, "description": "Quick rug risk scan - honeypot, liquidity lock, ownership, and contract flags"},
    "rugmaps_analysis": {"price_usd": 0.02, "price_atoms": "20000", "category": "basic", "trial_free": 3, "description": "Holder distribution analysis - risk scoring, dump patterns, concentration"},
    "social_feed": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 5, "description": "Social sentiment feed - what crypto Twitter and Telegram are saying"},
    "socialfi_resolve": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 3, "description": "Resolve social identity - ENS names, Farcaster profiles, linked addresses"},
    "threat_check": {"price_usd": 0.02, "price_atoms": "20000", "category": "basic", "trial_free": 3, "description": "Threat intelligence check - known scams, malicious patterns, risk scoring"},
    "token_detail": {"price_usd": 0.02, "price_atoms": "20000", "category": "basic", "trial_free": 3, "description": "Full token intelligence - market cap, volume, liquidity, holders, risk flags"},
    "token_price": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 5, "description": "Get real-time token price with consensus from multiple sources"},
    "trending": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 5, "description": "Trending tokens across chains - hottest movers right now"},
    "tvl": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 5, "description": "DeFi TVL data - protocol-level totals, chain breakdowns, yields"},
    "wallet_balance": {"price_usd": 0.01, "price_atoms": "10000", "category": "basic", "trial_free": 3, "description": "Check any wallet's balance across chains - multi-chain support"},
    "wallet_labels": {"price_usd": 0.02, "price_atoms": "20000", "category": "basic", "trial_free": 3, "description": "Identify who owns a wallet - entity labels, tags, and known affiliations"},
    # ── market ──
    "airdrop_check": {"price_usd": 0.05, "price_atoms": "50000", "category": "market", "trial_free": 2, "description": "airdrop_check"},
    "arbitrage_scan": {"price_usd": 0.05, "price_atoms": "50000", "category": "market", "trial_free": 2, "description": "arbitrage_scan"},
    "chain_health": {"price_usd": 0.05, "price_atoms": "50000", "category": "market", "trial_free": 0, "description": "chain_health"},
    "defi_yield_scanner": {"price_usd": 0.08, "price_atoms": "80000", "category": "market", "trial_free": 1, "description": "defi_yield_scanner"},
    "gas_forecast": {"price_usd": 0.05, "price_atoms": "50000", "category": "market", "trial_free": 0, "description": "gas_forecast"},
    "liquidity_depth": {"price_usd": 0.05, "price_atoms": "50000", "category": "market", "trial_free": 2, "description": "liquidity_depth"},
    "market_overview": {"price_usd": 0.05, "price_atoms": "50000", "category": "market", "trial_free": 0, "description": "market_overview"},
    "pulse": {"price_usd": 0.01, "price_atoms": "10000", "category": "market", "trial_free": 3, "description": "pulse"},
    "unlock_calendar": {"price_usd": 0.03, "price_atoms": "30000", "category": "market", "trial_free": 3, "description": "unlock_calendar"},
    # ── api ──
    "human-execute": {"price_usd": 0.02, "price_atoms": "20000", "category": "api", "trial_free": 2, "description": "Human-in-the-loop execution - wallet-based payment for manual crypto investigation tasks"},
    "mcp-proxy": {"price_usd": 0.01, "price_atoms": "10000", "category": "api", "trial_free": 5, "description": "MCP protocol proxy - route tool calls through the x402 payment layer"},
    # ── monitoring ──
    "token_watch_alerts": {"price_usd": 0.0, "price_atoms": "0", "category": "monitoring", "trial_free": 999, "description": "token_watch_alerts"},
    "token_watch_check": {"price_usd": 0.03, "price_atoms": "30000", "category": "monitoring", "trial_free": 5, "description": "One-shot token status check - current LP, price, volume, and rug risk warnings"},
    "token_watch_create": {"price_usd": 0.05, "price_atoms": "50000", "category": "monitoring", "trial_free": 3, "description": "Set token monitoring watch - alerts when LP drops, price changes, or rug indicators detected"},
    "token_watch_list": {"price_usd": 0.0, "price_atoms": "0", "category": "monitoring", "trial_free": 999, "description": "token_watch_list"},
    "webhook_list": {"price_usd": 0.0, "price_atoms": "0", "category": "monitoring", "trial_free": 999, "description": "List registered webhooks for an address"},
    "webhook_register": {"price_usd": 0.02, "price_atoms": "20000", "category": "monitoring", "trial_free": 2, "description": "Register webhook URL for real-time monitoring alerts - rug pulls, whale moves, price crashes"},
    # ── bundle ──
    "forensic_pack": {"price_usd": 0.35, "price_atoms": "350000", "category": "bundle", "trial_free": 1, "description": "Forensic Investigation Pack - valuation + OSINT + report at 33% discount"},
}

__all__ = ["MARKET_BASIC_TOOLS"]
