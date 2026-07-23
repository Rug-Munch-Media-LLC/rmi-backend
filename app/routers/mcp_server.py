"""
Rug Munch Intelligence MCP Server v3.1 (Spec Compliant)

MCP protocol version: 2024-11-05
Transport: Streamable HTTP (POST /mcp)
Discovery: /.well-known/mcp | /.well-known/mcp.json | /llms.txt
Tool listing: GET /mcp/tools | POST /mcp (tools/list)
Tool execution: POST /mcp/call/{tool_id} | POST /mcp (tools/call)
x402 payments: /.well-known/x402

Changes from v3.0:
- Added /.well-known/mcp (no .json) per MCP discovery convention
- Added POST /mcp JSON-RPC handler (initialize, tools/list, tools/call)
- Added inputSchema to every tool (JSON Schema objects array)
- Fix: tool "name" now uses tool_id not description
- Fix: facilitators count dynamically loaded from registry
- Fix: chains count from CHAIN_USDC not hardcoded
- Added proper error codes per MCP spec
- Added CORS headers for browser-based MCP clients
"""

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.core.logging import get_logger

logger = get_logger("rmi_mcp_v3")
router = APIRouter(tags=["mcp"])

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "Rug Munch Intelligence"
SERVER_VERSION = "3.2.0"


def _get_tools() -> dict[str, Any]:
    try:
        from app.routers.x402_enforcement import CHAIN_USDC, TOOL_PRICES

        return {"prices": dict(TOOL_PRICES), "chains": dict(CHAIN_USDC)}
    except Exception:
        return {"prices": {}, "chains": {}}


def _get_facilitator_count() -> int:
    try:
        from app.facilitators.base import get_registry

        return len(get_registry().get_all())
    except Exception:
        return 10


def _desc(tool_id: str, pricing: dict) -> str:
    d = pricing.get("description", "")
    if d and d != tool_id and len(d) > 10:
        return d
    FALLBACKS = {
        "airdrop_check": "Verify airdrop legitimacy -- contract audit, distribution analysis, scam pattern detection. Know if an airdrop is real or a wallet drainer before connecting.",
        "airdrop_finder": "Discover active and upcoming airdrops across all major chains. Eligibility checks, value estimation, claim deadlines, and Sybil detection.",
        "all_in_one": "All-in-One Audit -- comprehensive security scan: rug pull, honeypot, clone detection, contract audit, and ownership analysis in a single call.",
        "alpha_digest": "Alpha digest -- curated crypto alpha from top-performing wallets, on-chain signals, sentiment spikes, and accumulation patterns.",
        "arbitrage_scan": "Cross-chain and cross-DEX arbitrage scanner. Find price discrepancies across exchanges for instant profit opportunities.",
        "bundler_detect": "MEV bundler detector -- sandwich attacks, frontrunning, backrunning patterns on Solana and EVM chains.",
        "catalog": "Full tool catalog -- list every RMI tool with pricing, chain support, trial availability, and descriptions.",
        "clone_detect": "Clone contract detector -- bytecode similarity analysis, function matching, known scam template identification.",
        "deployer_history": "Deployer history investigation -- every token this wallet has launched, success rate, known scam patterns, cross-chain activity.",
        "fresh_pair": "Fresh pair scanner -- detect newly created trading pairs, liquidity depth, ownership concentration, honeypot risk.",
        "insider_network": "Insider network mapper -- trace connected wallets, shared funding sources, coordinated trading patterns across addresses.",
        "intelligence_pack": "Intelligence Pack -- whale tracking + smart money + wallet clustering at 29% discount.",
        "kol_performance": "KOL performance tracker -- measure influencer call accuracy, average ROI after calls, follower quality score.",
        "liquidity_depth": "Liquidity depth analyzer -- order book depth, slippage estimation, market impact across DEXs and chains.",
        "liquidity_flow": "Liquidity flow tracker -- track where capital is moving across chains, pools, and protocols.",
        "liquidity_migration": "Liquidity migration detector -- tokens moving pools, chains, or protocols. Often a rug pull precursor signal.",
        "listing_predictor": "Exchange listing predictor -- on-chain signals suggesting imminent CEX or DEX listing based on accumulation patterns.",
        "meme_vibe_score": "Meme coin vibe score -- social virality, holder growth rate, community engagement metrics, and dump risk assessment.",
        "mev_alert": "MEV alert system -- real-time sandwich attack, frontrun, and arbitrage detection with wallet protection recommendations.",
        "mev_protection": "MEV protection checker -- verify if your transaction is protected from MEV extraction before submitting.",
        "portfolio_aggregate": "Portfolio aggregator -- combine multiple wallets into a single dashboard with consolidated PnL and asset allocation.",
        "profile_flip": "Profile flip detector -- sudden Twitter/X profile changes, domain swaps, or branding pivots before token launches or scams.",
        "protocol_risk": "Protocol risk assessment -- TVL stability, admin key analysis, upgrade patterns, oracle dependency, governance risk.",
        "rug_pull_predictor": "Rug pull predictor -- AI-powered risk scoring using 12+ signals: liquidity locks, ownership, holder distribution, social signals.",
        "scam_database": "Scam database lookup -- check addresses against known scam, phishing, honeypot, and rug pull databases.",
        "security_pack": "Security Pack -- honeypot + rug pull + audit + clone detection at 23% discount.",
        "sentiment_spike": "Sentiment spike detector -- real-time social media volume anomalies and sentiment shifts for any token.",
        "smart_money_alpha": "Smart money alpha -- real-time alerts when top-performing wallets enter new positions.",
        "sniper_alert": "Sniper alert system -- detect sniper bots entering new token launches in real-time.",
        "syndicate_scan": "Syndicate scanner -- identify coordinated trading groups, wash trading rings, pump-and-dump networks.",
        "syndicate_track": "Syndicate tracker -- follow known syndicate wallets, monitor their current positions and exit patterns.",
        "token_age": "Token age verifier -- contract creation date, migration history, proxy upgrades, and deployment patterns.",
        "unlock_calendar": "Token unlock calendar -- track vesting schedules, team token unlocks, upcoming dilution events.",
        "wallet_graph": "Wallet graph analysis -- visualize transaction flows, identify money laundering patterns and entity relationships.",
        "wallet_pnl": "Wallet PnL calculator -- realized/unrealized gains, win rate, ROI, Sharpe ratio, and complete trade history.",
        "wash_trading": "Wash trading detector -- identify fake volume, self-trades, artificial market activity across NFTs and tokens.",
        "whale_accumulation": "Whale accumulation detector -- track large wallet accumulation and distribution patterns.",
        "whale_profile": "Whale profile -- complete analysis: holdings, strategy classification, historical performance, influence score.",
        "whale_scan": "Whale scanner -- real-time whale activity across chains. Large transfers, exchange deposits, accumulation signals.",
        # ── Tools with pricing.description == tool_id (no real description) ──
        "anomaly": "Anomaly detector -- flag unusual transaction patterns, price manipulations, and suspicious on-chain behavior across all chains.",
        "audit": "Smart contract audit -- static analysis, vulnerability detection, ownership risks, and function-level security assessment.",
        "bridge_security": "Cross-chain bridge security audit -- liquidity verification, admin key analysis, exploit history, and withdrawal safety checks.",
        "chain_health": "Chain health monitor -- network congestion, gas trends, validator status, RPC reliability, and mempool depth analysis.",
        "cluster": "Wallet cluster analysis -- identify linked addresses via shared funding, transaction patterns, and behavioral heuristics.",
        "comprehensive_audit": "Comprehensive audit -- full security scan combining honeypot detection, rug pull prediction, contract analysis, and risk scoring.",
        "copy_trade_finder": "Copy trade discovery -- find wallets with proven track records, filter by ROI, win rate, and strategy type for mirroring.",
        "defi_yield_scanner": "DeFi yield scanner -- scan across chains for highest APY opportunities with risk assessment and sustainability scoring.",
        "forensics": "On-chain forensics -- deep transaction tracing, money flow analysis, and entity identification for wallet investigation.",
        "gas_forecast": "Gas price forecast -- predict optimal transaction timing with historical gas trends and real-time network conditions.",
        "honeypot_check": "Honeypot detector -- verify if a token contract prevents selling, uses hidden mint functions, or traps buyer funds.",
        "insider": "Insider activity tracker -- detect pre-launch accumulation, team wallet movements, and privileged information signals.",
        "launch": "Token launch analyzer -- new token launch evaluation covering initial liquidity, deployer reputation, and early holder distribution.",
        "launch_intel": "Launch intelligence -- comprehensive new token assessment: pre-launch signals, launch mechanics, and post-launch performance.",
        "market_overview": "Market overview -- aggregate crypto market data: total cap, sector performance, dominance shifts, and macro trend signals.",
        "nft_wash_detector": "NFT wash trading detector -- identify self-bought NFTs, circular transfers, and artificial floor price inflation.",
        "portfolio_tracker": "Portfolio tracker -- monitor wallet holdings over time with performance metrics, rebalancing suggestions, and risk alerts.",
        "pulse": "Market pulse -- real-time snapshot of market sentiment, trading volume, and directional momentum across top tokens.",
        "risk_monitor": "Risk monitor -- continuous risk assessment for watched tokens with automated alerts on liquidity drops and ownership changes.",
        "rugshield": "RugShield instant check -- rapid safety assessment: honeypot status, liquidity lock, owner renunciation, and top holder concentration.",
        "sentiment": "Crypto sentiment analysis -- aggregate social sentiment from Twitter, Reddit, and news sources for any token or market sector.",
        "smartmoney": "Smart money tracker -- follow wallets with consistently high ROI, their current positions, entry/exit patterns, and strategy.",
        "sniper_detect": "Sniper bot detector -- identify automated sniping wallets that buy within seconds of launch, track their patterns and targets.",
        "social_signal": "Social signal aggregator -- combine Twitter volume, Reddit sentiment, Telegram buzz, and influencer mentions into one score.",
        "token_comparison": "Token comparison -- side-by-side analysis of two or more tokens: risk scores, holder distribution, liquidity, and performance metrics.",
        "token_deep_dive": "Token deep dive -- exhaustive single-token analysis covering every signal: security, social, whale, on-chain, and market data.",
        "token_watch_alerts": "Token watch alerts -- push notifications for watched tokens when LP changes, whale moves, or rug indicators are detected.",
        "token_watch_list": "Token watch list -- view all your monitored tokens with current status, risk level, and recent alert summaries.",
        "tw_profile": "X/Twitter profile analysis -- crypto account credibility, engagement metrics, bot score, and influence rating.",
        "tw_search": "X/Twitter crypto search -- find tweets, sentiment, and discussions about any token, wallet, or market event.",
        "tw_timeline": "X/Twitter timeline feed -- curated crypto timeline from top analysts, whales, and project accounts.",
        "urlcheck": "URL security checker -- scan crypto URLs for phishing patterns, credential harvesting, and known scam infrastructure.",
        "wallet": "Wallet analysis -- comprehensive wallet assessment: holdings, risk score, labels, transaction patterns, and entity identification.",
        "wallet_graph": "Wallet transaction graph -- visualize address relationships, money flows, and interaction networks for forensic investigation.",  # noqa: F601
        "wallet_pnl": "Wallet PnL calculator -- realized and unrealized gains, win rate, average ROI, and complete trade performance history.",  # noqa: F601
        "wash_trading": "Wash trading detector -- identify artificial volume, self-trades, and coordinated buy-sell patterns across DEXs.",  # noqa: F601
        "whale": "Whale tracker -- monitor large holder activity, recent transfers, accumulation trends, and wallet classification.",
        "whale_accumulation": "Whale accumulation monitor -- detect when large wallets are building positions, track accumulation rate and entry timing.",  # noqa: F601
        "whale_profile": "Whale profile -- deep analysis of a whale wallet: strategy classification, historical performance, holdings breakdown, and influence.",  # noqa: F601
    }
    return FALLBACKS.get(
        tool_id,
        f"{tool_id.replace('_', ' ').title()} -- real-time crypto intelligence and security analysis.",
    )


# Build input schemas per tool based on known parameter patterns
def _input_schema(tool_id: str) -> dict:
    """Return MCP-compliant inputSchema for a tool."""
    # Universal parameters most tools accept
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
            "url": {
                "type": "string",
                "description": "URL or contract address to analyze",
            },
            "chain": {
                "type": "string",
                "description": "Blockchain (base, ethereum, solana, etc.)",
            },
        },
        "required": ["url"],
    }
    base_none = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    # Tool-specific schemas
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
                "chain": {"type": "string", "description": "Blockchain to query"},
            },
            "required": ["address"],
        },
        "smart_money_alpha": {
            "type": "object",
            "properties": {
                "wallet": {"type": "string", "description": "Wallet address to track"},
                "chain": {"type": "string", "description": "Blockchain"},
                "limit": {"type": "integer", "description": "Number of results (default 10)"},
            },
        },
        "sentiment_spike": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Token name or contract address"},
                "chain": {"type": "string", "description": "Blockchain"},
            },
        },
        "catalog": base_none,
    }
    # For per-chain variants (e.g., wallet_solana), return base_address with chain pre-filled
    # For expanded tools not in schemas dict, return base_address as default
    return schemas.get(tool_id, base_address)


# ================================================================
# CORS MIDDLEWARE (for browser-based MCP clients)
# ================================================================

# CORS headers are added per-route in response headers.
# APIRouter doesn't support middleware - CORS is handled in each endpoint.


# ================================================================
# MCP DISCOVERY ENDPOINTS
# ================================================================


def _build_discovery() -> dict:
    """Build the MCP server discovery document."""
    data = _get_tools()
    tools = data["prices"]
    chains = data["chains"]
    fac_count = _get_facilitator_count()
    cats = sorted({p.get("category", "analysis") for p in tools.values()})

    return {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "description": f"{len(tools)} crypto intelligence tools -- real-time scam detection, wallet forensics, whale tracking, contract auditing, market analysis. {len(chains)} blockchains, micropayments via x402.",
        "protocol": "mcp",
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "vendor": {
            "name": "Rug Munch Intelligence",
            "url": "https://rugmunch.io",
            "github": "https://github.com/Rug-Munch-Media-LLC",
        },
        "homepage": "https://rugmunch.io",
        "documentation": "https://rugmunch.io/mcp-docs",
        "repository": "https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp",
        "endpoint": "https://mcp.rugmunch.io/mcp",
        "icon": "https://rugmunch.io/logo.png",
        "transports": ["http"],
        "authentication": {
            "type": "x402",
            "description": "Pay-per-use micropayments. 1-5 free trials per tool. Connect wallet for additional free calls. USDC on Base/Solana, USDT/USDC on TRON, BTC via Mempool.space, EUR/SEPA via Asterpay. Full refund if no data returned.",
            "discovery_url": "https://mcp.rugmunch.io/.well-known/x402",
            "wallet_providers": {
                "evm": {
                    "description": "Any EIP-7702 compatible wallet: MetaMask, Coinbase Wallet, WalletConnect, Rabby, OKX Wallet, Trust Wallet, Frame, Rainbow",
                    "chains": [
                        "base",
                        "ethereum",
                        "bsc",
                        "polygon",
                        "arbitrum",
                        "optimism",
                        "avalanche",
                        "fantom",
                        "gnosis",
                    ],
                    "facilitators": ["coinbase_cdp", "eip7702", "cloudflare_x402", "payai"],
                },
                "solana": {
                    "description": "Phantom, Solflare, Backpack, Coinbase Wallet, Magic Eden",
                    "chains": ["solana"],
                    "facilitators": ["coinbase_cdp", "payai"],
                },
                "tron": {
                    "description": "TronLink, TokenPocket, OKX Wallet",
                    "chains": ["tron"],
                    "facilitators": ["tron_selfverify"],
                },
                "bitcoin": {
                    "description": "Any Bitcoin wallet (1-confirmation)",
                    "chains": ["bitcoin"],
                    "facilitators": ["bitcoin_selfverify"],
                },
                "fiat": {
                    "description": "EUR/SEPA bank transfer via Asterpay",
                    "chains": ["sepa"],
                    "facilitators": ["asterpay"],
                },
            },
        },
        "capabilities": {"tools": True, "resources": False, "prompts": False},
        "directories": {
            "smithery": "https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence",
            "glama": "https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence",
            "mcp_so": "https://mcp.so/server/rug-munch-intelligence",
            "github": "https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp",
            "huggingface": "https://huggingface.co/cryptorugmunch/rug-munch-intelligence",
        },
        "integrations": {
            "openai_agents": {
                "discovery": "https://mcp.rugmunch.io/.well-known/x402",
                "endpoint": "https://mcp.rugmunch.io/mcp",
                "protocol": "x402",
                "description": "OpenAI Agents SDK with x402 payment support. Automatic 402 handling via coinbase_cdp or eip7702 facilitator.",
            },
            "claude_desktop": {
                "endpoint": "https://mcp.rugmunch.io/mcp",
                "protocol": "mcp-streamable-http",
                "description": "Claude Desktop via MCP Streamable HTTP transport. Configure in claude_desktop_config.json.",
            },
            "anthropic_agents": {
                "discovery": "https://mcp.rugmunch.io/.well-known/x402",
                "endpoint": "https://mcp.rugmunch.io/mcp",
                "protocol": "x402+mcp",
                "description": "Anthropic Agents with x402 micropayments. Use the Streamable HTTP transport.",
            },
            "openlang": {
                "discovery": "https://mcp.rugmunch.io/.well-known/x402",
                "endpoint": "https://mcp.rugmunch.io/mcp",
                "protocol": "x402",
                "description": "OpenLang MCP client with automatic x402 payment flow.",
            },
            "cursor": {
                "endpoint": "https://mcp.rugmunch.io/mcp",
                "protocol": "mcp-streamable-http",
                "description": "Cursor IDE via MCP. Add as MCP server in Settings > MCP.",
            },
        },
        "stats": {
            "total_tools": len(tools),
            "categories": cats,
            "chains": sorted(chains.keys()),
            "chain_count": len(chains),
            "facilitators": fac_count,
            "free_trials": "1-5 calls per tool, fingerprint-gated",
            "pricing": "$0.01 - $0.40 per call",
            "updated_at": datetime.now(UTC).isoformat(),
        },
        # Top-level fields for directory scrapers (Glama, mcp.so, Smithery)
        "categories": cats,
        "blockchains": sorted(chains.keys()),
        "facilitator_count": fac_count,
        "tools_count": len(tools),
        # Compact tool list so directory scrapers see tools immediately
        "tools": [
            {
                "name": tid,
                "description": _desc(tid, t)[:200],
                "category": t.get("category", "analysis"),
                "price_usd": t.get("price_usd", 0.01),
                "trial_free": t.get("trial_free", 1),
            }
            for tid, t in sorted(tools.items())
        ],
    }


@router.get("/.well-known/mcp")
@router.get("/.well-known/mcp.json")
async def mcp_discovery() -> Any:
    return _build_discovery()


@router.get("/.well-known/ai-plugin.json")
async def ai_plugin_manifest() -> dict[str, str]:
    data = _get_tools()
    count = len(data["prices"])
    return {
        "schema_version": "v1",
        "name_for_human": SERVER_NAME,
        "name_for_model": "rug_munch_intelligence",
        "description_for_human": f"{SERVER_NAME} -- crypto intelligence: scam detection, wallet forensics, whale tracking, contract auditing. {count} tools, {len(data['chains'])} chains.",
        "description_for_model": f"Use for crypto security: check tokens for scams, honeypots, rug pulls. Analyze wallets for PnL, clusters, insider trading. Track whales, smart money, syndicates. Audit smart contracts. Market intelligence: fear & greed, chain health, gas forecasts, DeFi yields, arbitrage. Social signals: Twitter/X sentiment, KOL performance. {count} tools, {len(data['chains'])} chains. Free trials + x402 micropayments.",
        "auth": {"type": "none"},
        "api": {"type": "openapi", "url": "https://mcp.rugmunch.io/openapi.json"},
        "logo_url": "https://rugmunch.io/logo.png",
        "contact_email": "mcp@rugmunch.io",
        "legal_info_url": "https://rugmunch.io/terms",
    }


@router.get("/llms.txt")
async def llms_txt() -> Any:
    data = _get_tools()
    prices = data["prices"]
    chains = data["chains"]
    fac_count = _get_facilitator_count()
    cats = {}
    for _tool_id, pricing in prices.items():
        cat = pricing.get("category", "analysis")
        cats[cat] = cats.get(cat, 0) + 1
    cat_lines = [f"- {c.title()} ({n})" for c, n in sorted(cats.items())]
    chain_list = ", ".join(sorted(chains.keys()))

    return Response(
        content=f"""# Rug Munch Intelligence -- MCP Server
> {len(prices)} crypto intelligence tools. {len(chains)} chains. Free trials + x402 micropayments.

## Quick Start
- MCP Endpoint: https://mcp.rugmunch.io/mcp
- Discovery: https://mcp.rugmunch.io/.well-known/mcp
- Payment: https://mcp.rugmunch.io/.well-known/x402
- Docs: https://rugmunch.io/mcp-docs
- GitHub: https://github.com/cryptorugmuncher/rug-munch-intelligence

## Directory Listings
- Smithery: https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence
- Glama: https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence
- mcp.so: https://mcp.so/server/rug-munch-intelligence
- Open WebUI: https://openwebui.com/t/cryptorugmuncher/rug-munch-intelligence

## How It Works
- Free trial -- 1-5 calls per tool, no payment. Fingerprint-gated anti-abuse.
- Pay per use -- USDC on {len(chains)} chains (Base, Solana, Ethereum, BSC, TRON, Bitcoin, more). $0.01-$0.40 per call.
- Instant refund -- Full refund if tool returns no data. Request within 48h.
- {fac_count} payment facilitators with automatic fallback. Instant settlement on Base, Solana, BNB.

## Tool Categories
{chr(10).join(cat_lines)}

## Payment Chains
{chain_list}

## Integration
curl https://mcp.rugmunch.io/.well-known/mcp
curl https://mcp.rugmunch.io/mcp/tools
""",
        media_type="text/plain; charset=utf-8",
    )


# ================================================================
# TOOL CATALOG
# ================================================================


def _build_tools_list() -> tuple[Any, Any, Any, Any]:
    """Build the full MCP-compatible tools list."""
    data = _get_tools()
    prices = data["prices"]
    chains_data = data["chains"]
    fac_count = _get_facilitator_count()

    tools = {}
    cats = {}
    # DataBus tools route through x402-databus, others through x402-tools
    from app.routers.x402_databus_tools import X402_TOOL_PRICING as DATABUS_TOOLS

    databus_ids = set(DATABUS_TOOLS.keys())
    from app.routers.x402_tools import TOOL_ALIASES

    for tool_id, pricing in sorted(prices.items()):
        cat = pricing.get("category", "analysis")
        cats[cat] = cats.get(cat, 0) + 1
        real_tool = TOOL_ALIASES.get(tool_id, tool_id)
        endpoint = (
            f"/api/v1/x402-databus/{tool_id}"
            if tool_id in databus_ids
            else f"/api/v1/x402-tools/{real_tool}"
        )
        tools[tool_id] = {
            "name": tool_id,  # MCP spec: name is the tool ID
            "description": _desc(tool_id, pricing),
            "category": cat,
            "inputSchema": _input_schema(tool_id),
            "price_usd": float(pricing.get("price_usd", 0.01)),
            "trial_free": int(pricing.get("trial_free", 1)),
            "chains": sorted(chains_data.keys()),
            "endpoint": endpoint,
            "method": pricing.get("method", "POST")
            if isinstance(pricing.get("method"), str)
            else "POST",
            "databus": tool_id in databus_ids,
        }

    # Add databus-only tools (not in TOOL_PRICES enforcement dict)
    for tool_id, pricing in sorted(DATABUS_TOOLS.items()):
        if tool_id not in tools:
            cat = pricing.get("category", "data")
            cats[cat] = cats.get(cat, 0) + 1
            tools[tool_id] = {
                "name": tool_id,
                "description": _desc(tool_id, pricing)
                if pricing.get("description")
                else f"{tool_id.replace('_', ' ').title()} -- DataBus-powered crypto intelligence.",
                "category": cat,
                "inputSchema": _input_schema(tool_id),
                "price_usd": float(pricing.get("price_usd", 0.05)),
                "trial_free": int(pricing.get("trial_free", 1)),
                "chains": sorted(chains_data.keys()),
                "endpoint": f"/api/v1/x402-databus/{tool_id}",
                "method": "POST",
                "databus": True,
            }

    return tools, cats, chains_data, fac_count


@router.get("/mcp/tools")
async def mcp_tools_list(request: Request) -> dict[str, str]:
    """Every tool in the RMI platform -- full catalog with MCP-compliant schemas."""
    tools, cats, chains_data, fac_count = _build_tools_list()

    trial_info = None
    try:
        from app.routers.x402_enforcement import check_trial, get_client_id

        cid = get_client_id(request)
        remaining = {}
        for tid in _get_tools()["prices"]:
            can, rem = check_trial(tid, cid)
            if rem > 0 or can:
                remaining[tid] = {"can_trial": can, "remaining": rem}
        trial_info = {
            "client_id": cid[:20] + "...",
            "tools_with_trials": len(remaining),
            "trials": remaining,
        }
    except Exception:
        pass

    return {
        "server": f"{SERVER_NAME} MCP v{SERVER_VERSION}",
        "homepage": "https://rugmunch.io",
        "github": "https://github.com/cryptorugmuncher/rug-munch-intelligence",
        "directories": {
            "smithery": "https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence",
            "glama": "https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence",
        },
        "total_tools": len(tools),
        "categories": dict(sorted(cats.items())),
        "chains": sorted(chains_data.keys()),
        "chain_count": len(chains_data),
        "facilitators": fac_count,
        "pricing": "$0.01-$0.40/call. Most tools $0.05. Bundles save 23-33%.",
        "free_trials": "1-5 calls per tool. Fingerprint-gated. Wallet required after 1 free call.",
        "payment": {
            "protocol": "x402",
            "discovery": "/.well-known/x402",
            "tokens": ["USDC", "USDT", "BTC", "EUR"],
            "chains": sorted(chains_data.keys()),
        },
        "refund": "Full refund if tool returns no data. Within 48h via POST /api/v1/x402/refund.",
        "tools": tools,
        "trial_status": trial_info,
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/tools")
async def mcp_tools_mcp_router_format(request: Request) -> dict:
    """Serve tools in mcp-router compatible format: {tools: {service: [tools]}}.

    This endpoint is consumed by the x402 Cloudflare Workers (BACKEND_MCP + '/tools').
    It replaces the external mcp-router.rugmunch.io dependency that was returning
    HTTP 522 due to Cloudflare proxy loop (Worker -> CF-proxied domain).
    """
    tools_data, _cats, _chains_data, _fac_count = _build_tools_list()

    # Group tools by category (matching mcp-router's {serviceName: [tools]} format)
    # Each tool has: name, description, parameters(inputSchema), tier, category
    services: dict[str, list] = {}
    for tool_id, tool_def in tools_data.items():
        svc = tool_def.get("category", "general")
        if svc not in services:
            services[svc] = []
        services[svc].append(
            {
                "name": tool_id,
                "description": tool_def.get("description", tool_def.get("name", tool_id)),
                "parameters": tool_def.get("inputSchema", {}),
                "tier": "free" if tool_def.get("trial_free", 0) > 0 else "paid",
                "category": svc,
                "chains": tool_def.get("chains", []),
                "price_usd": tool_def.get("price_usd", 0.01),
                "endpoint": tool_def.get("endpoint", f"/api/v1/x402-tools/{tool_id}"),
            }
        )

    return {"tools": services}


@router.get("/mcp/capabilities")
async def mcp_capabilities() -> dict[str, str]:
    data = _get_tools()
    fac_count = _get_facilitator_count()
    return {
        "server": f"{SERVER_NAME} MCP v{SERVER_VERSION}",
        "homepage": "https://rugmunch.io",
        "github": "https://github.com/cryptorugmuncher/rug-munch-intelligence",
        "capabilities": {"tools": True, "resources": False, "prompts": False, "streaming": False},
        "protocols": ["x402"],
        "payment": {
            "required": False,
            "trial_available": True,
            "trial_calls": "1-5 per tool",
            "paid": f"$0.01-$0.40 via x402 on {len(data['chains'])} chains",
        },
        "facilitators": fac_count,
        "updated_at": datetime.now(UTC).isoformat(),
    }


# ================================================================
# MCP JSON-RPC ENDPOINT (Streamable HTTP)
# ================================================================


@router.post("/mcp")
async def mcp_jsonrpc(request: Request) -> Any:
    """MCP Streamable HTTP transport - handle JSON-RPC requests.

    Methods: initialize, tools/list, tools/call, resources/list, prompts/list, ping
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None,
            },
        )

    method = body.get("method", "")
    req_id = body.get("id")
    params = body.get("params", {})

    # ── initialize ─────────────────────────────────────────
    if method == "initialize":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": True},
                        "resources": {},
                        "prompts": {},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }
        )

    # ── ping ────────────────────────────────────────────────
    if method == "ping":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})

    # ── tools/list ──────────────────────────────────────────
    if method == "tools/list":
        tools_data, _cats, _chains_data, _fac_count = _build_tools_list()
        tools_list = []
        for tool_id, info in tools_data.items():
            # Dot-notation naming: category.tool_id for Smithery quality score
            category = info.get("category", "analysis")
            dot_name = f"{category}.{tool_id}"
            tools_list.append(
                {
                    "name": dot_name,
                    "description": info["description"],
                    "inputSchema": info["inputSchema"],
                    "outputSchema": {
                        "type": "object",
                        "properties": {
                            "result": {
                                "type": "object",
                                "description": "Tool-specific result data - varies by tool. Contains analysis, scores, metrics, or fetched data.",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["ok", "error", "no_data"],
                                "description": "Result status: ok (success), error (failure), no_data (no results found - eligible for refund)",
                            },
                            "error": {
                                "type": "string",
                                "description": "Error message if status is error",
                            },
                            "metadata": {
                                "type": "object",
                                "properties": {
                                    "tool": {"type": "string", "description": "Tool name executed"},
                                    "chain": {"type": "string", "description": "Blockchain used"},
                                    "elapsed_ms": {
                                        "type": "number",
                                        "description": "Execution time in milliseconds",
                                    },
                                    "trial_used": {
                                        "type": "boolean",
                                        "description": "Whether a free trial was consumed",
                                    },
                                },
                            },
                        },
                        "required": ["result", "status"],
                    },
                    "annotations": {
                        "title": info.get("display_name", info["name"].replace("_", " ").title()),
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": True,
                        "category": info["category"],
                        "price_usd": info["price_usd"],
                        "trial_free": info["trial_free"],
                        "chains": info["chains"],
                    },
                }
            )
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tools_list},
            }
        )

    # ── resources/list ──────────────────────────────────────
    if method == "resources/list":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"resources": []},
            }
        )

    # ── prompts/list ───────────────────────────────────────
    if method == "prompts/list":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"prompts": []},
            }
        )

    # ── ai.smithery/events/list ─────────────────────────────
    if method == "ai.smithery/events/list":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"events": []},
            }
        )

    # ── tools/call ──────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Resolve dot-notation names (category.tool_id) to internal tool_id
        internal_name = tool_name
        if "." in tool_name:
            internal_name = tool_name.split(".", 1)[1]  # strip category prefix
            # If the stripped name doesn't exist in prices, try the original
            data_check = _get_tools()
            if internal_name not in data_check["prices"] and tool_name in data_check["prices"]:
                internal_name = tool_name  # fall back to original name

        # Validate tool exists
        data = _get_tools()
        if internal_name not in data["prices"]:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Tool '{tool_name}' not found. {len(data['prices'])} tools available.",
                    },
                }
            )

        # Proxy to internal endpoint
        import httpx

        url = f"http://localhost:8000/api/v1/x402-tools/{internal_name}"
        headers = {"Content-Type": "application/json", "User-Agent": "RMI-MCP-JSONRPC/3.1"}
        # Forward payment and identity headers from the original request
        for h in (
            "x-pay",
            "X-Pay",
            "X-Device-Id",
            "x-device-id",
            "Authorization",
            "x-wallet-address",
            "X-Wallet-Address",
            "x-turnstile-token",
            "X-Turnstile-Token",
        ):
            val = request.headers.get(h)
            if val:
                headers[h] = val
        for h in ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP"):
            val = request.headers.get(h)
            if val:
                headers[h] = val

        try:
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(url, json=arguments, headers=headers)
                if resp.status_code == 402:
                    # Payment required - return the payment info as tool result
                    result = resp.json()
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [{"type": "text", "text": json.dumps(result)}],
                                "isError": False,
                            },
                        }
                    )
                result = (
                    resp.json()
                    if "application/json" in (resp.headers.get("content-type", ""))
                    else {"data": resp.text}
                )
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(result)}],
                            "isError": resp.status_code >= 400,
                        },
                    }
                )
        except httpx.ConnectError:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": "Backend unavailable"},
                }
            )
        except Exception as e:
            logger.error(f"MCP JSON-RPC tools/call failed: {tool_name}: {e}")
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": f"Tool execution failed: {str(e)[:200]}"},
                }
            )

    # ── Unknown method ──────────────────────────────────────
    return JSONResponse(
        status_code=400,
        content={
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        },
    )


@router.options("/mcp")
async def mcp_options() -> Any:
    """CORS preflight for MCP endpoint."""
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Pay, X-Device-Id, X-Wallet-Address",
            "Access-Control-Max-Age": "86400",
        },
    )


# ================================================================
# TOOL EXECUTION (REST API)
# ================================================================


@router.post("/mcp/call/{tool_id}")
async def mcp_call_tool(tool_id: str, request: Request) -> Any:
    """Execute any tool. Requires x402 payment or free trial."""
    data = _get_tools()
    if tool_id not in data["prices"] and tool_id not in ("list", "tools", "catalog"):
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{tool_id}' not found. {len(data['prices'])} tools available -- see /mcp/tools",
        )

    try:
        body = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )
    except Exception:
        body = {}

    import httpx

    url = f"http://localhost:8000/api/v1/x402-tools/{tool_id}"
    try:
        headers = {}
        for h in (
            "x-pay",
            "X-Pay",
            "X-Device-Id",
            "x-device-id",
            "User-Agent",
            "Authorization",
            "x-wallet-address",
            "X-Wallet-Address",
            "x-turnstile-token",
            "X-Turnstile-Token",
        ):
            val = request.headers.get(h)
            if val:
                headers[h] = val
        if "User-Agent" not in headers:
            headers["User-Agent"] = "RMI-MCP-Proxy/3.1"
        for h in ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP"):
            val = request.headers.get(h)
            if val:
                headers[h] = val
        ct = request.headers.get("content-type")
        if ct:
            headers["content-type"] = ct

        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(url, json=body, headers=headers)
            result = (
                resp.json()
                if "application/json" in (resp.headers.get("content-type", ""))
                else {"data": resp.text}
            )
            rh = {}
            for h in (
                "X-RMI-Payment",
                "X-RMI-Trial",
                "X-RMI-Trial-Remaining",
                "X-RMI-Refund-Flagged",
            ):
                if resp.headers.get(h):
                    rh[h] = resp.headers[h]
            rh["Access-Control-Allow-Origin"] = "*"
            return (
                JSONResponse(content=result, status_code=resp.status_code, headers=rh)
                if rh
                else JSONResponse(content=result, status_code=resp.status_code)
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Backend unavailable") from None
    except Exception as e:
        logger.error(f"MCP tool failed: {tool_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Tool execution failed: {str(e)[:200]}") from e


# ═══════════════════════════════════════════════════════════════════════════
# PLATFORM MANIFEST - Auto-updating source of truth
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/mcp/manifest")
async def platform_manifest() -> Any:
    """Complete platform manifest - auto-generated from live tool counts."""
    from app.caching_shield.platform_manifest import get_platform_manifest

    return get_platform_manifest()


@router.get("/mcp/skills")
async def agent_skills() -> Any:
    """Agent skills, workflows, anti-abuse rules, and starter prompts."""
    from app.caching_shield.agent_skills import get_agent_skills

    return get_agent_skills()


@router.get("/mcp/membership")
async def membership_plans() -> Any:
    """Membership tiers, scan packs, streams, research, batch processing."""
    from app.caching_shield.membership_plans import get_membership_catalog

    return get_membership_catalog()


# ═══════════════════════════════════════════════════════════════════════════
# EARNINGS DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/mcp/earnings")
async def earnings_dashboard() -> dict:
    """Live earnings dashboard - wallet balances, revenue by source."""
    from app.caching_shield.earnings_tracker import fetch_wallet_earnings, get_earnings_report

    wallets = await fetch_wallet_earnings()
    report = get_earnings_report()
    return {**report, "wallet_balances": wallets}


@router.get("/mcp/earnings/wallets")
async def earnings_wallets() -> Any:
    """Current payment wallet balances."""
    from app.caching_shield.earnings_tracker import fetch_wallet_earnings

    return await fetch_wallet_earnings()


@router.get("/mcp/earnings/sources")
async def earnings_by_source() -> Any:
    """Revenue broken down by tool, chain, and facilitator."""
    from app.caching_shield.earnings_tracker import get_revenue_by_source

    return get_revenue_by_source()


# ═══════════════════════════════════════════════════════════════════════════
# DAILY MARKET RUNDOWN
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/mcp/news")
async def mcp_news_feed(
    category: str | None = None,
    sentiment: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """Aggregated crypto news with sentiment analysis."""
    from app.caching_shield.market_rundown import get_articles

    return await get_articles(
        category=category or "",
        sentiment=sentiment or "",
        source=source or "",
        limit=limit,
        offset=offset,
    )


@router.get("/mcp/news/summary")
async def market_summary(force: bool = False) -> Any:
    """AI-generated daily market rundown (DeepSeek V4 Pro, cached 24h)."""
    from app.caching_shield.market_rundown import generate_market_summary

    return await generate_market_summary(force=force)


@router.get("/mcp/news/categories")
async def mcp_news_categories() -> dict:
    """Available news categories."""
    from app.caching_shield.market_rundown import get_categories

    return {"categories": get_categories()}


@router.get("/mcp/news/sources")
async def news_sources() -> dict:
    """Available news sources."""
    from app.caching_shield.market_rundown import get_sources

    return {"sources": get_sources()}


@router.post("/mcp/news/vote")
async def mcp_news_vote(data: dict) -> Any:
    """Vote up/down on an article."""
    from app.caching_shield.market_rundown import vote

    return await vote(data.get("article_id", ""), data.get("direction", "up"))


@router.post("/mcp/news/comment")
async def mcp_news_comment(data: dict) -> Any:
    """Add a comment to an article."""
    from app.caching_shield.market_rundown import comment

    return await comment(data.get("article_id", ""), data.get("user", "anon"), data.get("text", ""))


@router.get("/mcp/news/comments/{article_id}")
async def mcp_news_comments(article_id: str) -> Any:
    """Get comments for an article."""
    from app.caching_shield.market_rundown import get_comments

    return await get_comments(article_id)


@router.get("/mcp/daily-data")
async def daily_market_data() -> Any:
    """Enhanced daily data - price action, sentiment, security, whales, prediction markets."""
    from app.caching_shield.daily_data import get_daily_rundown_data

    return await get_daily_rundown_data()


# ═══════════════════════════════════════════════════════════════════════════
# RMI NEWS NETWORK - 30 sources, community interaction
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/news/feed")
async def news_feed(
    category: str | None = None,
    sentiment: str | None = None,
    tier: int | None = None,
    sort: str = "latest",
    limit: int = 50,
    offset: int = 0,
    impact: str | None = None,
    source: str | None = None,
) -> Any:
    """Main news feed with all filters."""
    from app.caching_shield.news_network import fetch_all, get_feed

    await fetch_all(max_per_source=5)
    return get_feed(
        category=category or "",
        sentiment=sentiment or "",
        tier=tier or 0,
        sort=sort,
        limit=limit,
        offset=offset,
        impact=impact or "",
        source=source or "",
    )


@router.get("/news/categories")
async def news_categories() -> dict:
    from app.caching_shield.news_network import fetch_all, get_categories

    await fetch_all(max_per_source=3)
    return {"categories": get_categories()}


@router.post("/news/vote")
async def news_vote(data: dict) -> Any:
    from app.caching_shield.news_network import vote_article

    return vote_article(data.get("article_id", ""), data.get("direction", "up"))


@router.post("/news/comment")
async def news_comment(data: dict) -> Any:
    from app.caching_shield.news_network import add_comment

    return add_comment(data.get("article_id", ""), data.get("user", "anon"), data.get("text", ""))


@router.get("/news/comments/{article_id}")
async def news_comments(article_id: str) -> dict:
    from app.caching_shield.news_network import get_comments

    return {"comments": get_comments(article_id)}


@router.post("/news/bookmark")
async def news_bookmark(data: dict) -> Any:
    from app.caching_shield.news_network import bookmark

    return bookmark(data.get("article_id", ""))


@router.get("/news/search")
async def news_search(q: str = "", limit: int = 20) -> dict:
    from app.caching_shield.news_network import search_articles

    return {"results": search_articles(q, limit)}


@router.get("/news/daily-data")
async def daily_data() -> Any:
    from app.caching_shield.daily_data import get_daily_rundown_data

    return await get_daily_rundown_data()


# ═══════════════════════════════════════════════════════════════════════════
# SOCIAL FEED - X/Twitter + Reddit
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/news/social")
async def social_feed(limit_twitter: int = 30, limit_reddit: int = 20) -> Any:
    """Combined X/Twitter + Reddit crypto feed - cached, Nitter fallback."""
    from app.caching_shield.social_feed import get_social_feed

    return await get_social_feed(limit_twitter, limit_reddit)


@router.get("/news/social/twitter")
async def twitter_feed(limit: int = 30) -> Any:
    """Top 50 crypto X/Twitter accounts - via Nitter (free, cached)."""
    from app.caching_shield.social_feed import get_twitter_feed

    return await get_twitter_feed(limit)


@router.get("/news/social/reddit")
async def reddit_feed(limit: int = 20) -> Any:
    """Top crypto subreddits - free, no auth."""
    from app.caching_shield.social_feed import get_reddit_feed

    return await get_reddit_feed(limit)


@router.get("/news/social/accounts")
async def social_accounts() -> dict:
    """List of monitored X accounts with profile pics."""
    from app.caching_shield.social_feed import get_top_accounts

    return {"accounts": get_top_accounts()}
