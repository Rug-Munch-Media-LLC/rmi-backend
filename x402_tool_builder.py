#!/usr/bin/env python3
"""
x402 Tool Gap Analysis & Builder
Identifies connector APIs not yet exposed as x402 tools and generates tool definitions.
"""

# All connectors with their capabilities
CONNECTORS = {
    "gmgn": {
        "name": "GMGN Token Intelligence",
        "description": "Trending tokens, KOL tracking, smart money movements, new pair detection from GMGN. Find what's hot before the crowd.",
        "price": "$0.05",
        "category": "intelligence",
        "trialFree": 1,
        "api_key_file": "gmgn_api_key",
        "tools": [
            {
                "id": "gmgn_trending",
                "name": "GMGN Trending",
                "desc": "Get trending tokens on GMGN with volume, price change, and KOL activity. Find what the smart money is buying.",
                "price": "0.05",
                "cat": "intelligence",
                "trial": 1,
            },
            {
                "id": "gmgn_kol",
                "name": "KOL Tracker",
                "desc": "Track Key Opinion Leader wallets — see what top traders are buying and selling in real-time.",
                "price": "0.08",
                "cat": "intelligence",
                "trial": 1,
            },
            {
                "id": "gmgn_new_pairs",
                "name": "New Pair Scanner",
                "desc": "Scan newly created trading pairs on GMGN — get in early on promising launches.",
                "price": "0.05",
                "cat": "launchpad",
                "trial": 2,
            },
        ],
    },
    "arkham": {
        "name": "Arkham Intelligence",
        "description": "Entity labeling, wallet attribution, institutional-grade blockchain intelligence.",
        "price": "$0.10",
        "category": "intelligence",
        "trialFree": 0,
        "api_key_file": "arkham_api_key",
        "tools": [
            {
                "id": "arkham_entity",
                "name": "Entity Lookup",
                "desc": "Look up entity labels and ownership for any wallet address. Find out who's behind the wallet.",
                "price": "0.10",
                "cat": "intelligence",
                "trial": 0,
            },
            {
                "id": "arkham_portfolio",
                "name": "Entity Portfolio",
                "desc": "Get full portfolio holdings and transaction history for labeled entities.",
                "price": "0.15",
                "cat": "intelligence",
                "trial": 0,
            },
        ],
    },
    "nansen": {
        "name": "Nansen Analytics",
        "description": "Smart money tracking, token God mode, on-chain analytics.",
        "price": "$0.15",
        "category": "intelligence",
        "trialFree": 0,
        "api_key_file": "nansen_api_key",
        "tools": [
            {
                "id": "nansen_smart_money",
                "name": "Nansen Smart Money",
                "desc": "Track smart money wallets — see what profitable traders are accumulating before pumps.",
                "price": "0.15",
                "cat": "intelligence",
                "trial": 0,
            },
            {
                "id": "nansen_token_god",
                "name": "Token God Mode",
                "desc": "Nansen Token God Mode — full holder breakdown, exchange flows, smart money holdings.",
                "price": "0.20",
                "cat": "analysis",
                "trial": 0,
            },
        ],
    },
    "birdeye": {
        "name": "Birdeye Token Discovery",
        "description": "Multi-chain token discovery, trending, and market data.",
        "price": "$0.03",
        "category": "market",
        "trialFree": 2,
        "api_key_file": "birdeye_api_key",
        "tools": [
            {
                "id": "birdeye_trending",
                "name": "Birdeye Trending",
                "desc": "Multi-chain trending tokens from Birdeye — volume, price action, liquidity.",
                "price": "0.03",
                "cat": "market",
                "trial": 2,
            },
            {
                "id": "birdeye_token",
                "name": "Token Overview",
                "desc": "Complete token overview — price, volume, liquidity, holders, social links from Birdeye.",
                "price": "0.05",
                "cat": "market",
                "trial": 1,
            },
        ],
    },
    "defillama": {
        "name": "DeFiLlama Analytics",
        "description": "TVL data, protocol rankings, yield information, chain metrics.",
        "price": "$0.01",
        "category": "market",
        "trialFree": 3,
        "api_key_file": None,  # Free API
        "tools": [
            {
                "id": "defillama_tvl",
                "name": "Chain TVL",
                "desc": "Get Total Value Locked (TVL) for any chain or protocol. Track DeFi ecosystem health.",
                "price": "0.01",
                "cat": "market",
                "trial": 3,
            },
            {
                "id": "defillama_protocol",
                "name": "Protocol Data",
                "desc": "Full protocol analytics — TVL history, revenue, fees, treasury from DeFiLlama.",
                "price": "0.01",
                "cat": "market",
                "trial": 3,
            },
            {
                "id": "defillama_yields",
                "name": "Yield Opportunities",
                "desc": "Find best yield opportunities across DeFi protocols. APY, TVL, risk indicators.",
                "price": "0.03",
                "cat": "market",
                "trial": 2,
            },
        ],
    },
    "etherscan": {
        "name": "Etherscan Explorer",
        "description": "Contract source code, verification status, transaction history, ABI extraction.",
        "price": "$0.02",
        "category": "security",
        "trialFree": 3,
        "api_key_file": "etherscan_api_key",
        "tools": [
            {
                "id": "etherscan_contract",
                "name": "Contract Source",
                "desc": "Get verified contract source code and ABI from Etherscan. Check if a contract is verified.",
                "price": "0.02",
                "cat": "security",
                "trial": 3,
            },
            {
                "id": "etherscan_tx",
                "name": "Transaction History",
                "desc": "Get full transaction history for any address from Etherscan with decoded inputs.",
                "price": "0.02",
                "cat": "analysis",
                "trial": 3,
            },
        ],
    },
    "coingecko": {
        "name": "CoinGecko Market Data",
        "description": "Comprehensive crypto market data — prices, market caps, trending, categories.",
        "price": "$0.01",
        "category": "market",
        "trialFree": 5,
        "api_key_file": "coingecko_api_key_pro",
        "tools": [
            {
                "id": "coingecko_price",
                "name": "Token Price",
                "desc": "Get current price, market cap, volume, and 24h change for any token from CoinGecko.",
                "price": "0.01",
                "cat": "market",
                "trial": 5,
            },
            {
                "id": "coingecko_trending",
                "name": "CoinGecko Trending",
                "desc": "Get CoinGecko trending tokens — most searched and visited coins in the last 24h.",
                "price": "0.01",
                "cat": "market",
                "trial": 5,
            },
            {
                "id": "coingecko_categories",
                "name": "Market Categories",
                "desc": "Browse CoinGecko categories — meme coins, DeFi, L2s, AI tokens with market data.",
                "price": "0.01",
                "cat": "market",
                "trial": 5,
            },
        ],
    },
}

# Count tools
total = 0
for name, conn in CONNECTORS.items():
    tools = conn["tools"]
    total += len(tools)
    key_status = "✅" if conn["api_key_file"] else "🆓 Free"
    print(f"\n{name}: {len(tools)} tools {key_status}")
    for t in tools:
        print(f"  {t['id']:25s} ${t['price']} [{t['cat']}] {t['desc'][:60]}")

print(f"\n{'=' * 60}")
print(f"TOTAL NEW TOOLS: {total}")
print("PREVIOUS TOTAL: 49")
print(f"NEW TOTAL: {49 + total}")
