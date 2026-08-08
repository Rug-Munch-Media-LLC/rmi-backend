"""
RMI Platform Manifest - Single source of truth for all platform descriptions.

Every external-facing surface reads from here: MCP discovery, docs,
directory listings, READMEs, Smithery, Glama, mcp.so, Open WebUI.

Update ONCE here, everything else auto-syncs.
"""

from __future__ import annotations
from typing import Any

from datetime import UTC, datetime

from app.caching_shield.agent_skills_extended import get_all_agent_skills
from app.caching_shield.membership_plans import get_membership_catalog
from app.caching_shield.tool_registry import TOOL_COUNTS
from app.routers.x402_enforcement import CHAIN_USDC, TOOL_PRICES


def get_platform_manifest() -> dict:
    """The ONE source of truth. Every platform surface reads from this."""
    tools = dict(TOOL_PRICES)
    counts = TOOL_COUNTS
    membership = get_membership_catalog()
    skills = get_all_agent_skills()

    return {
        # ═══ IDENTITY ═══
        "name": "Rug Munch Intelligence",
        "tagline": "The Bloomberg Terminal of Shitcoins",
        "version": "3.3.0",
        "updated": datetime.now(UTC).isoformat(),
        # ═══ TOOL COUNTS (auto from tool_registry) ═══
        "tools": {
            "total": counts["total"],
            "paid": len(tools),
            "with_free_trials": len(tools),  # every paid tool has free trials
            "free_trial_calls": "1-5 per tool, fingerprint-gated anti-abuse",
            "local_mcp": counts["local_mcp_svm"] + counts["local_mcp_evm"],
            "free_public": counts["free_mcp_boar"],
            "categories": len({p.get("category", "analysis") for p in tools.values()}),
            "chains": len(CHAIN_USDC),
            "facilitators": 8,
        },
        # ═══ PRICING ═══
        "pricing": {
            "individual": "$0.01 - $0.40 per call",
            "scan_packs": {
                "count": membership["stats"]["total_bundles"],
                "range": "$0.09 - $0.17",
                "discount": "50-53% off individual tools",
            },
            "memberships": {
                "tiers": membership["stats"]["subscription_tiers"],
                "range": "$4.99 - $199.99/month",
                "daily_calls": "50 - 5,000",
                "discount": "60-90% vs pay-per-use",
            },
            "streams": {
                "count": membership["stats"]["total_streams"],
                "range": "$0.30 - $0.75/hour or $5.99 - $14.99/day",
            },
            "research": {
                "count": membership["stats"]["total_research"],
                "range": "$0.25 - $1.50 per report",
            },
            "batch": {
                "count": membership["stats"]["total_batch"],
                "discount": "75-90% off bulk scanning",
            },
            "refund_policy": "Full automatic refund within 48h if tool returns no data",
        },
        # ═══ SKILLS ═══
        "agent_skills": {
            "total": len(skills["skills"]),
            "categories": {
                "security_vetting": [
                    "token_vetting",
                    "scam_investigation",
                    "rugpull_forensics",
                    "compliance_screen",
                ],
                "trading_execution": [
                    "launch_sniper",
                    "mev_avoidance",
                    "market_maker",
                    "portfolio_defense",
                ],
                "alpha_discovery": [
                    "alpha_discovery",
                    "whale_tracking",
                    "cex_listing_predictor",
                    "insider_trading",
                ],
                "defi_yield": ["defi_yield_optimizer", "bridge_monitor", "dao_governance"],
                "nft_influencer": ["nft_sniper", "kols_detector", "airdrop_hunting"],
            },
            "agent_prompts": len(skills["agent_prompts"]),
        },
        # ═══ ACCESS ═══
        "endpoints": {
            "mcp": "https://mcp.rugmunch.io/mcp",
            "discovery": "https://mcp.rugmunch.io/.well-known/mcp",
            "tools": "https://mcp.rugmunch.io/mcp/tools",
            "membership": "https://mcp.rugmunch.io/mcp/membership",
            "skills": "https://mcp.rugmunch.io/mcp/skills",
            "docs": "https://rugmunch.io/docs",
            "investigate": "https://rugmunch.io/investigate",
        },
        # ═══ INTEGRATIONS ═══
        "integrations": {
            "mcp_clients": [
                "Claude Desktop",
                "Cursor",
                "Windsurf",
                "Cline",
                "OpenAI Agents",
                "LangChain",
            ],
            "sdks": [
                "Python (pip install rmi-agent-sdk)",
                "TypeScript (npm install @rugmunch/agent-sdk)",
            ],
            "directories": {
                "smithery": "https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence",
                "glama": "https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence",
                "mcp_so": "https://mcp.so/server/rug-munch-intelligence",
                "github": "https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp",
            },
        },
        # ═══ WHAT'S NEW ═══
        "changelog": [
            "18 agent skills with workflow guides and anti-abuse rules",
            "4 membership tiers with daily call limits (60-90% discount)",
            "4 scan packs with 50-53% off individual tools",
            "4 real-time streaming feeds (WebSocket + webhook delivery)",
            "4 deep research report products",
            "3 batch scanning products (75-90% off bulk)",
            "3 AI-optimized data feeds for LLM consumption",
            "85 local MCP tools (Solana RPC + EVM across 86 networks)",
            "50 free Boar blockchain tools (ETH, ENS, contracts)",
            "Multi-provider caching shield on every data call",
        ],
        # ═══ DESCRIPTIONS (formatted for different contexts) ═══
        "descriptions": {
            "short": f"{counts['total']} crypto intelligence tools with free trials. Token security, wallet forensics, whale tracking, market data. Pay-per-use via x402 micropayments. Scan packs and membership tiers available.",
            "medium": f"Rug Munch Intelligence provides {counts['total']} crypto intelligence tools across {len(CHAIN_USDC)} blockchains. Every tool includes free trials (1-5 calls). Scan packs from $0.09 (53% off). Membership tiers from $4.99/month. Real-time streams, deep research reports, batch scanning, and AI-optimized data feeds. Full refund if no data returned. 18 agent skills with workflow guides included.",
            "long": f"Rug Munch Intelligence is a unified crypto intelligence platform serving {counts['total']} tools. Every data call routes through a multi-layer caching shield with automatic provider fallback across 20+ data sources.\n\nTOOLS: Token security (rug pulls, honeypots, audits), wallet intelligence (PnL, clustering, insider networks), whale tracking, market data, DeFi analytics, social signals.\n\nPRICING: Pay-per-use from $0.01. Scan packs from $0.09 (50-53% off). Membership tiers from $4.99-$199.99/month (60-90% discount). Real-time streams from $0.30/hr. Deep research reports from $0.25. Batch scanning at 75-90% off.\n\nFREE TRIALS: Every paid tool includes 1-5 free calls. Fingerprint-gated anti-abuse. Monthly reset.\n\nAGENT SKILLS: 18 workflow guides teaching agents how to vet tokens, track whales, investigate scams, discover alpha, and manage portfolios.\n\nCHAINS: {', '.join(sorted(CHAIN_USDC.keys())[:8])} and 80+ more via local MCP servers.\n\nACCESS: MCP endpoint at https://mcp.rugmunch.io/mcp. REST API at https://rugmunch.io/api. Python and TypeScript SDKs available.",
        },
    }


# ═══ AUTO-SYNC ═══


def sync_to_mcp_discovery() -> dict:
    """Generate the MCP discovery JSON fragment that should be merged into _build_discovery()."""
    m = get_platform_manifest()
    return {
        "description": m["descriptions"]["short"],
        "tools_count": m["tools"]["total"],
        "free_trial_tools": m["tools"]["with_free_trials"],
        "pricing": m["pricing"],
        "skills_count": m["agent_skills"]["total"],
        "membership_endpoint": m["endpoints"]["membership"],
        "skills_endpoint": m["endpoints"]["skills"],
    }


def sync_to_readme() -> str:
    """Generate a README.md section."""
    m = get_platform_manifest()
    return m["descriptions"]["long"]


def sync_to_directory_listing() -> dict:
    """Generate fields for Smithery/Glama/mcp.so listings."""
    m = get_platform_manifest()
    return {
        "name": m["name"],
        "description": m["descriptions"]["short"],
        "tools_count": m["tools"]["total"],
        "chains": m["tools"]["chains"],
        "pricing": "Free trials + pay-per-use + memberships",
        "version": m["version"],
        "endpoint": m["endpoints"]["mcp"],
    }
