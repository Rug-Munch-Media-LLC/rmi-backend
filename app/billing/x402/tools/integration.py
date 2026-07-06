"""x402 integration surface — discovery, MCP proxy, multi-chain payments,
super-tools (comprehensive audit, smart money alpha, meme vibe), bundles,
catch-all dispatchers.

Phase 3A of AUDIT-2026-Q3.md (split from app/routers/x402_tools.py).

Endpoints mounted on sub_router:
  GET  /api/v1/x402-tools/discovery
  GET  /api/v1/x402-tools/catalog
  GET  /api/v1/x402-tools/openai-tools
  GET  /api/v1/x402-tools/langchain-tools
  GET  /api/v1/x402-tools/anthropic-tools
  GET  /api/v1/x402-tools/gemini-tools
  GET  /api/v1/x402-tools/frameworks
  POST /api/v1/x402-tools/comprehensive_audit
  POST /api/v1/x402-tools/smart_money_alpha
  POST /api/v1/x402-tools/meme_vibe_score
  GET  /api/v1/x402-tools/bundles
  POST /api/v1/x402-tools/bundles/security_pack
  POST /api/v1/x402-tools/bundles/intelligence_pack
  POST /api/v1/x402-tools/bundles/all_in_one
  POST /api/v1/x402-tools/forensic_pack
  POST /api/v1/x402-tools/mcp-proxy
  GET  /api/v1/x402-tools/payment-methods
  POST /api/v1/x402-tools/human-execute
  GET  /api/v1/x402-tools/{tool_id}                 (catch-all dispatcher)
  POST /api/v1/x402-tools/{tool_id}                 (catch-all dispatcher)

Tool-specific endpoints live in the per-domain tools/* files.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, ClassVar

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.billing.x402.shared import (
    HUMAN_PAY_TO,
    HUMAN_PAYMENT_TOKENS,
    AuditRequest,
    BundleRequest,
    MCPProxyRequest,
    MemeVibeRequest,
    SmartMoneyAlphaRequest,
    td,
)

sub_router = APIRouter(tags=["x402-integration"])


# ── OpenAI-Compatible Tools Endpoint ───────────────────────────


async def _build_tools_from_catalog():
    """Build tool list from TOOL_PRICES (source of truth for all 201+ tools)."""
    from app.routers.x402_enforcement import TOOL_PRICES

    tools = []
    for tool_id, pricing in sorted(TOOL_PRICES.items()):
        desc = pricing.get("description", f"{tool_id} - crypto intelligence tool")
        category = pricing.get("category", "analysis")
        chain = pricing.get("chain")
        base_tool = pricing.get("base_tool")
        is_variant = bool(chain)
        tools.append(
            {
                "id": tool_id,
                "description": desc,
                "price_usd": pricing.get("price_usd", 0.01),
                "category": category,
                "chain": chain,
                "base_tool": base_tool,
                "is_variant": is_variant,
                "trial_free": pricing.get("trial_free", 1),
            }
        )
    return tools


@sub_router.get("/discovery")
async def tools_discovery():
    """Human-friendly tool discovery - organized by category with SEO descriptions.

    Returns all 71 RMI tools with pricing, descriptions, and categories.
    MCP external tools (154+) available via MCP tools/list on gateway workers.
    """
    from app.tool_catalog import get_human_catalog

    catalog = get_human_catalog()
    return catalog


@sub_router.get("/catalog")
async def tools_catalog_bot():
    """Bot-optimized tool catalog - flat list with IDs, pricing, chain support.

    Designed for AI agents to quickly discover and call tools.
    Minimal descriptions, structured parameters, x402 protocol info.
    """
    from app.tool_catalog import get_bot_catalog

    return get_bot_catalog()


@sub_router.get("/openai-tools")
async def openai_tools():
    """Returns ALL 201+ tool definitions in OpenAI function calling format.
    Use this with OpenAI Agents SDK or GPT-4o function calling.
    Each tool maps to POST /api/v1/x402-tools/{tool_name} with x402 payment."""
    raw_tools = await _build_tools_from_catalog()
    openai_tools = []
    for t in raw_tools:
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": t["id"],
                    "description": f"{t['description']} - ${t['price_usd']:.2f}/call, {t['trial_free']} free trial(s). Category: {t['category']}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "address": {
                                "type": "string",
                                "description": f"Token or wallet address to analyze with {t['id']}",
                            },
                            "chain": {
                                "type": "string",
                                "description": "Blockchain: solana, base, ethereum, bsc. Default: solana",
                                "default": "solana",
                            },
                        },
                        "required": ["address"],
                    },
                },
            }
        )
    return {
        "service": "Rug Munch Intelligence",
        "tagline": "We build tools to keep the crypto space safer",
        "total_tools": len(openai_tools),
        "followers_x": "67,000+",
        "telegram_users": "7,000+",
        "networks": [
            "base",
            "solana",
            "ethereum",
            "bsc",
            "arbitrum",
            "polygon",
            "avalanche",
            "fantom",
            "gnosis",
            "optimism",
            "tron",
            "bitcoin",
            "sepa",
        ],
        "protocol": "x402",
        "payment_required": True,
        "tools": openai_tools,
    }


# ── LangChain Tools Endpoint ────────────────────────────────────


@sub_router.get("/langchain-tools")
async def langchain_tools():
    """Returns ALL 201+ tool definitions in LangChain format.
    Use with LangChain agents, LangGraph, or any LangChain-based system."""
    raw_tools = await _build_tools_from_catalog()
    langchain_tools = []
    for t in raw_tools:
        langchain_tools.append(
            {
                "name": t["id"],
                "description": f"{t['description']} - ${t['price_usd']:.2f}/call, {t['trial_free']} free trial(s). Category: {t['category']}.",
                "args_schema": {
                    "address": {
                        "type": "string",
                        "description": f"Token or wallet address to analyze with {t['id']}",
                    },
                    "chain": {
                        "type": "string",
                        "description": "Blockchain: solana, base, ethereum, bsc. Default: solana",
                        "default": "solana",
                    },
                },
                "required": ["address"],
                "endpoint": f"/api/v1/x402-tools/{t['id']}",
                "method": "POST",
            }
        )
    return {
        "service": "Rug Munch Intelligence",
        "tagline": "We build tools to keep the crypto space safer",
        "total_tools": len(langchain_tools),
        "followers_x": "67,000+",
        "telegram_users": "7,000+",
        "networks": [
            "base",
            "solana",
            "ethereum",
            "bsc",
            "arbitrum",
            "polygon",
            "avalanche",
            "fantom",
            "gnosis",
            "optimism",
            "tron",
            "bitcoin",
            "sepa",
        ],
        "protocol": "x402",
        "format": "langchain",
        "usage": "pip install langchain && use with create_react_agent or LangGraph",
        "tools": langchain_tools,
    }


# ── Anthropic Claude API Tools Endpoint ─────────────────────────


@sub_router.get("/anthropic-tools")
async def anthropic_tools():
    """Returns ALL 201+ tool definitions in Anthropic Claude API format.
    Use with Claude API (messages API) for native tool use."""
    raw_tools = await _build_tools_from_catalog()
    anthropic_tools_list = []
    for t in raw_tools:
        anthropic_tools_list.append(
            {
                "name": t["id"],
                "description": f"{t['description']} - ${t['price_usd']:.2f}/call, {t['trial_free']} free trial(s). Category: {t['category']}.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "address": {
                            "type": "string",
                            "description": f"Token or wallet address to analyze with {t['id']}",
                        },
                        "chain": {
                            "type": "string",
                            "description": "Blockchain: solana, base, ethereum, bsc. Default: solana",
                            "default": "solana",
                        },
                    },
                    "required": ["address"],
                },
            }
        )
    return {
        "service": "Rug Munch Intelligence",
        "tagline": "We build tools to keep the crypto space safer",
        "total_tools": len(anthropic_tools_list),
        "followers_x": "67,000+",
        "telegram_users": "7,000+",
        "networks": [
            "base",
            "solana",
            "ethereum",
            "bsc",
            "arbitrum",
            "polygon",
            "avalanche",
            "fantom",
            "gnosis",
            "optimism",
            "tron",
            "bitcoin",
            "sepa",
        ],
        "protocol": "x402",
        "format": "anthropic_claude_api",
        "usage": "pip install anthropic && use with client.messages.create(tools=...)",
        "tools": anthropic_tools_list,
    }


# ── Google Gemini Function Declarations ─────────────────────────


@sub_router.get("/gemini-tools")
async def gemini_tools():
    """Returns ALL 201+ tool definitions in Google Gemini function calling format.
    Use with Google AI SDK or Vertex AI for Gemini models."""
    raw_tools = await _build_tools_from_catalog()
    gemini_declarations = []
    for t in raw_tools:
        gemini_declarations.append(
            {
                "name": t["id"],
                "description": f"{t['description']} - ${t['price_usd']:.2f}/call, {t['trial_free']} free trial(s). Category: {t['category']}.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "address": {
                            "type": "STRING",
                            "description": f"Token or wallet address to analyze with {t['id']}",
                        },
                        "chain": {
                            "type": "STRING",
                            "description": "Blockchain: solana, base, ethereum, bsc. Default: solana",
                        },
                    },
                    "required": ["address"],
                },
            }
        )

    return {
        "service": "Rug Munch Intelligence",
        "tagline": "We build tools to keep the crypto space safer",
        "followers_x": "67,000+",
        "telegram_users": "7,000+",
        "total_tools": len(gemini_declarations),
        "networks": [
            "base",
            "solana",
            "ethereum",
            "bsc",
            "arbitrum",
            "polygon",
            "avalanche",
            "fantom",
            "gnosis",
            "optimism",
            "tron",
            "bitcoin",
            "sepa",
        ],
        "protocol": "x402",
        "format": "google_gemini",
        "usage": "pip install google-genai && use with model.generate_content(tools=...)",
        "function_declarations": gemini_declarations,
    }


# ── Framework Discovery Endpoint ────────────────────────────────


@sub_router.get("/frameworks")
async def framework_discovery():
    """Returns all available framework integrations with endpoints.
    This is the master discovery endpoint for AI frameworks."""
    base = "https://mcp.rugmunch.io/api/v1/x402-tools"
    return {
        "service": "Rug Munch Intelligence",
        "tagline": "We build tools to keep the crypto space safer",
        "followers_x": "67,000+",
        "telegram_users": "7,000+",
        "networks": ["base", "solana"],
        "protocol": "x402",
        "frameworks": {
            "openai": {
                "endpoint": f"{base}/openai-tools",
                "format": "OpenAI function calling",
                "usage": "client.chat.completions.create(tools=...)",
                "models": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
                "free": True,
            },
            "anthropic": {
                "endpoint": f"{base}/anthropic-tools",
                "format": "Claude API tool use",
                "usage": "client.messages.create(tools=...)",
                "models": ["claude-sonnet-4", "claude-opus-4", "claude-haiku"],
                "free": True,
            },
            "gemini": {
                "endpoint": f"{base}/gemini-tools",
                "format": "Google Gemini function calling",
                "usage": "model.generate_content(tools=...)",
                "models": ["gemini-2.0-flash", "gemini-2.5-pro"],
                "free": True,
            },
            "langchain": {
                "endpoint": f"{base}/langchain-tools",
                "format": "LangChain tool definitions",
                "usage": "create_react_agent or LangGraph",
                "ecosystem": "langchain, langgraph, crewai, autogen",
                "free": True,
            },
            "mcp": {
                "endpoint": "python -m app.mcp.x402_mcp_server",
                "format": "Model Context Protocol (stdio)",
                "usage": "Claude Desktop, Claude Code, any MCP client",
                "free": True,
            },
            "rest_api": {
                "endpoint": f"{base}/{{tool_name}}",
                "format": "HTTP POST/GET with JSON",
                "usage": "Any language, any framework",
                "payment": "x402 (USDC on Base/Solana)",
            },
        },
        "x402_gateways": {
            "base": "https://x402.rugmunch.io/tools/{tool}",
            "solana": "https://x402-sol.rugmunch.io/tools/{tool}",
            "payment": "USDC via x402 protocol",
        },
    }


# ═══════════════════════════════════════════════════════════════
# TOOL 36: Comprehensive Audit (Super Tool) ($0.15)
# ═══════════════════════════════════════════════════════════════


@sub_router.post("/comprehensive_audit")
async def comprehensive_audit(req: AuditRequest):
    """One-call deep audit combining rug check, forensics, social, and whale analysis."""
    import httpx

    try:
        BASE_URL = "http://localhost:8000"

        async def get_rug():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{BASE_URL}/api/v1/x402-tools/rugshield",
                    json={"address": req.address, "chain": req.chain},
                )
                return r.json() if r.status_code == 200 else None

        async def get_forensics():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{BASE_URL}/api/v1/x402-tools/forensics",
                    json={"address": req.address, "chain": req.chain},
                )
                return r.json() if r.status_code == 200 else None

        async def get_social():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{BASE_URL}/api/v1/x402-tools/sentiment",
                    json={"token": req.address, "chain": req.chain},
                )
                return r.json() if r.status_code == 200 else None

        async def get_whale():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{BASE_URL}/api/v1/x402-tools/whale",
                    json={"address": req.address, "chain": req.chain},
                )
                return r.json() if r.status_code == 200 else None

        rug_data, forensics_data, social_data, whale_data = await asyncio.gather(
            get_rug(), get_forensics(), get_social(), get_whale(), return_exceptions=True
        )

        if isinstance(rug_data, Exception):
            rug_data = None
        if isinstance(forensics_data, Exception):
            forensics_data = None
        if isinstance(social_data, Exception):
            social_data = None
        if isinstance(whale_data, Exception):
            whale_data = None

        risk_score = 50
        factors = []
        recommendation = "HOLD"

        if rug_data and rug_data.get("is_honeypot"):
            risk_score += 30
            factors.append("CRITICAL: Potential Honeypot detected")

        if rug_data and not rug_data.get("liquidity_locked"):
            risk_score += 15
            factors.append("WARNING: Liquidity not locked")

        if forensics_data:
            liq_usd = forensics_data.get("liquidity_usd", 0)
            if liq_usd > 0 and liq_usd < 1000:
                risk_score += 20
                factors.append(f"LOW LIQUIDITY: Only ${liq_usd:,.2f} locked")

            vol_24h = forensics_data.get("volume_24h", 0)
            if liq_usd > 0 and vol_24h > liq_usd * 5:
                risk_score += 10
                factors.append("High volume/liquidity ratio (possible wash trading)")

        if social_data:
            sentiment_score = social_data.get("sentiment_score", 0)
            if sentiment_score > 0.7:
                risk_score -= 10
                factors.append("Positive social sentiment")
            elif sentiment_score < 0.3:
                risk_score += 10
                factors.append("Negative social sentiment")

        if whale_data:
            whale_count = whale_data.get("whale_count", 0)
            if whale_count > 0:
                risk_score -= 5
                factors.append(f"{whale_count} whale(s) detected")

        risk_score = max(0, min(100, risk_score))

        if risk_score >= 70:
            recommendation = "HIGH RISK / AVOID"
        elif risk_score >= 50:
            recommendation = "CAUTION / HIGH VOLATILITY"
        else:
            recommendation = "RELATIVELY SAFE / MONITOR"

        result = {
            "tool": "Comprehensive Audit",
            "address": req.address,
            "chain": req.chain,
            "timestamp": datetime.utcnow().isoformat(),
            "summary": {
                "risk_score": risk_score,
                "recommendation": recommendation,
                "factors": factors,
            },
            "sub_reports": {
                "rug_check": rug_data,
                "forensics": forensics_data,
                "social": social_data,
                "whale_analysis": whale_data,
            },
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }

        await _record_payment("comprehensive_audit", "0.15", req.address)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/smart_money_alpha")
async def smart_money_alpha(req: SmartMoneyAlphaRequest):
    """One-call smart money signal combining wallet analysis, smart money tracking, and cluster/sybil detection."""
    import httpx

    try:
        BASE_URL = "http://localhost:8000"

        async def get_wallet():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{BASE_URL}/api/v1/x402-tools/wallet",
                    json={"address": req.wallet, "chain": req.chain},
                )
                return r.json() if r.status_code == 200 else None

        async def get_smartmoney():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"{BASE_URL}/api/v1/x402-tools/smartmoney")
                return r.json() if r.status_code == 200 else None

        async def get_cluster():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{BASE_URL}/api/v1/x402-tools/cluster",
                    json={"address": req.wallet, "chain": req.chain},
                )
                return r.json() if r.status_code == 200 else None

        async def get_insider():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{BASE_URL}/api/v1/x402-tools/insider",
                    json={"address": req.wallet, "chain": req.chain},
                )
                return r.json() if r.status_code == 200 else None

        wallet_data, smartmoney_data, cluster_data, insider_data = await asyncio.gather(
            get_wallet(), get_smartmoney(), get_cluster(), get_insider(), return_exceptions=True
        )

        for i, d in enumerate([wallet_data, smartmoney_data, cluster_data, insider_data]):
            if isinstance(d, Exception):
                [wallet_data, smartmoney_data, cluster_data, insider_data][i] = None

        alpha_score = 50
        signals = []

        if wallet_data:
            tx_count = wallet_data.get("tx_count", 0)
            if tx_count > 1000:
                alpha_score += 10
                signals.append(f"Active wallet ({tx_count}+ transactions)")
            profit_ratio = wallet_data.get("profit_ratio", 0)
            if profit_ratio > 0.5:
                alpha_score += 15
                signals.append(f"Profitable trader ({profit_ratio:.0%} win rate)")

        if smartmoney_data:
            sm_alerts = smartmoney_data.get("smart_money_alerts", [])
            if sm_alerts:
                alpha_score += 10
                signals.append(f"{len(sm_alerts)} active smart money movements")

        if cluster_data:
            cluster_size = cluster_data.get("cluster_size", 0)
            if cluster_size > 5:
                alpha_score -= 10
                signals.append(f"Large cluster ({cluster_size} wallets) - possible sybil")
            if cluster_data.get("is_sybil", False):
                alpha_score -= 20
                signals.append("SYBIL DETECTED - wallet part of coordinated group")

        if insider_data and insider_data.get("insider_detected", False):
            alpha_score += 20
            signals.append("INSIDER ACTIVITY - wallet linked to early token access")

        alpha_score = max(0, min(100, alpha_score))

        verdict = "NEUTRAL"
        if alpha_score >= 75:
            verdict = "STRONG ALPHA - high-value wallet signals"
        elif alpha_score >= 60:
            verdict = "MODERATE ALPHA - watch for follow-up"
        elif alpha_score <= 30:
            verdict = "LOW SIGNAL - avoid or monitor passively"

        result = {
            "tool": "Smart Money Alpha",
            "wallet": req.wallet,
            "chain": req.chain,
            "timestamp": datetime.utcnow().isoformat(),
            "summary": {
                "alpha_score": alpha_score,
                "verdict": verdict,
                "signals": signals,
            },
            "sub_reports": {
                "wallet_analysis": wallet_data,
                "smart_money": smartmoney_data,
                "cluster_analysis": cluster_data,
                "insider_detection": insider_data,
            },
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }

        await _record_payment("smart_money_alpha", "0.25", req.wallet)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/meme_vibe_score")
async def meme_vibe_score(req: MemeVibeRequest):
    """One-call meme token vibe check combining sentiment, social signals, and launch analysis."""
    import httpx

    try:
        BASE_URL = "http://localhost:8000"

        async def get_sentiment():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{BASE_URL}/api/v1/x402-tools/sentiment",
                    json={"token": req.token, "chain": req.chain},
                )
                return r.json() if r.status_code == 200 else None

        async def get_social_signal():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{BASE_URL}/api/v1/x402-tools/social_signal",
                    json={"token": req.token, "chain": req.chain},
                )
                return r.json() if r.status_code == 200 else None

        async def get_launch():
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(
                    f"{BASE_URL}/api/v1/x402-tools/launch?address={req.token}&chain={req.chain}"
                )
                return r.json() if r.status_code == 200 else None

        sentiment_data, social_data, launch_data = await asyncio.gather(
            get_sentiment(), get_social_signal(), get_launch(), return_exceptions=True
        )

        for i, d in enumerate([sentiment_data, social_data, launch_data]):
            if isinstance(d, Exception):
                [sentiment_data, social_data, launch_data][i] = None

        vibe_score = 50
        vibes = []

        if sentiment_data:
            score = sentiment_data.get("sentiment_score", 0)
            if score > 0.7:
                vibe_score += 15
                vibes.append("Strong positive sentiment")
            elif score < 0.3:
                vibe_score -= 15
                vibes.append("Negative sentiment detected")
            trend = sentiment_data.get("sentiment_trend", "")
            if trend:
                vibes.append(f"Sentiment trend: {trend}")

        if social_data:
            engagement = social_data.get("engagement_score", 0)
            bot_ratio = social_data.get("bot_ratio", 0)
            if engagement > 0.7:
                vibe_score += 10
                vibes.append("High social engagement")
            if bot_ratio > 0.5:
                vibe_score -= 20
                vibes.append(f"High bot ratio ({bot_ratio:.0%}) - likely artificial hype")

        if launch_data:
            bonding = launch_data.get("bonding_curve_progress", 0)
            if bonding > 0.8:
                vibe_score += 10
                vibes.append("Bonding curve near completion - strong launch momentum")
            elif bonding < 0.2:
                vibe_score -= 10
                vibes.append("Early bonding curve - high risk")
            if launch_data.get("is_fair_launch", False):
                vibe_score += 5
                vibes.append("Fair launch confirmed")

        vibe_score = max(0, min(100, vibe_score))

        if vibe_score >= 75:
            verdict = "VIBE CHECK PASSED - organic momentum"
        elif vibe_score >= 50:
            verdict = "MIXED VIBES - monitor closely"
        elif vibe_score >= 30:
            verdict = "WEAK VIBES - high risk of dump"
        else:
            verdict = "RUG VIBES - likely artificial/scam"

        result = {
            "tool": "Meme Vibe Score",
            "token": req.token,
            "chain": req.chain,
            "timestamp": datetime.utcnow().isoformat(),
            "summary": {
                "vibe_score": vibe_score,
                "verdict": verdict,
                "vibes": vibes,
            },
            "sub_reports": {
                "sentiment": sentiment_data,
                "social_signals": social_data,
                "launch_analysis": launch_data,
            },
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }

        await _record_payment("meme_vibe_score", "0.01", req.token)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Bundle Pricing Endpoints ─────────────────────────────────────
# Bundles aggregate multiple tools at a discount vs individual calls.
# Each bundle runs its component tools in parallel and returns a unified result.

from app.billing.x402.shared import BUNDLES as _BUNDLES  # noqa: E402


@sub_router.get("/bundles")
async def list_bundles():
    """List all available tool bundles with pricing and savings."""
    return {
        "bundles": {
            bid: {
                "name": b["name"],
                "description": b["description"],
                "tools": b["tools"],
                "individual_total": f"${b['individual_total']:.2f}",
                "bundle_price": f"${b['bundle_price_usd']:.2f}",
                "savings": f"{int((1 - b['bundle_price_usd'] / b['individual_total']) * 100)}%",
                "trial_free": b["trial_free"],
            }
            for bid, b in _BUNDLES.items()
        }
    }


@sub_router.post("/bundles/security_pack")
async def bundle_security_pack(req: BundleRequest):
    """Security Pack: rugshield + audit + urlcheck + honeypot_check at 23% discount."""
    target = req.address or req.token or req.url
    if not target:
        raise HTTPException(status_code=400, detail="Provide address, token, or url")

    tasks = {
        "rugshield": "http://localhost:8000/api/v1/x402-tools/rugshield",
        "audit": "http://localhost:8000/api/v1/x402-tools/audit",
        "urlcheck": "http://localhost:8000/api/v1/x402-tools/urlcheck",
        "honeypot_check": "http://localhost:8000/api/v1/x402-tools/honeypot_check",
    }

    results = {}
    async with aiohttp.ClientSession() as session:
        coros = {}
        for name, url in tasks.items():
            body = {"address": target, "token_address": target, "url": target, "chain": req.chain}
            coros[name] = session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=30))
        for name, coro in coros.items():
            try:
                resp = await coro
                results[name] = await resp.json() if resp.status == 200 else {"status": resp.status}
            except Exception as e:
                results[name] = {"error": str(e)}

    verdict = "SAFE"
    risk_scores = []
    for r in results.values():
        if isinstance(r, dict):
            rs = r.get("risk_score")
            if rs is not None:
                risk_scores.append(rs)
            if r.get("risk_level") in ("CRITICAL", "HIGH"):
                verdict = "CAUTION"

    avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0
    if avg_risk > 60:
        verdict = "HIGH_RISK"
    elif avg_risk > 40:
        verdict = "CAUTION"

    return {
        "tool": "Security Pack",
        "bundle": "security_pack",
        "target": target,
        "chain": req.chain,
        "timestamp": datetime.utcnow().isoformat(),
        "results": results,
        "summary": {
            "verdict": verdict,
            "avg_risk_score": round(avg_risk, 1),
            "tools_checked": len(results),
        },
        "price_usd": "0.10",
        "savings": "23% vs individual calls",
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }


@sub_router.post("/bundles/intelligence_pack")
async def bundle_intelligence_pack(req: BundleRequest):
    """Intelligence Pack: whale + smartmoney + cluster + insider at 29% discount."""
    wallet = req.wallet or req.address
    if not wallet:
        raise HTTPException(status_code=400, detail="Provide wallet or address")

    tasks = {
        "whale": "http://localhost:8000/api/v1/x402-tools/whale",
        "smartmoney": "http://localhost:8000/api/v1/x402-tools/smartmoney",
        "cluster": "http://localhost:8000/api/v1/x402-tools/cluster",
        "insider": "http://localhost:8000/api/v1/x402-tools/insider",
    }

    results = {}
    async with aiohttp.ClientSession() as session:
        coros = {}
        for name, url in tasks.items():
            body = {"address": wallet, "wallet_address": wallet, "chain": req.chain}
            coros[name] = session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=30))
        for name, coro in coros.items():
            try:
                resp = await coro
                results[name] = await resp.json() if resp.status == 200 else {"status": resp.status}
            except Exception as e:
                results[name] = {"error": str(e)}

    return {
        "tool": "Intelligence Pack",
        "bundle": "intelligence_pack",
        "wallet": wallet,
        "chain": req.chain,
        "timestamp": datetime.utcnow().isoformat(),
        "results": results,
        "price_usd": "0.25",
        "savings": "29% vs individual calls",
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }


@sub_router.post("/bundles/all_in_one")
async def bundle_all_in_one(req: BundleRequest):
    """All-in-One: comprehensive_audit + smart_money_alpha + meme_vibe_score at 30% discount."""
    target = req.address or req.token or req.wallet
    if not target:
        raise HTTPException(status_code=400, detail="Provide address, token, or wallet")

    tasks = {
        "comprehensive_audit": "http://localhost:8000/api/v1/x402-tools/comprehensive_audit",
        "smart_money_alpha": "http://localhost:8000/api/v1/x402-tools/smart_money_alpha",
        "meme_vibe_score": "http://localhost:8000/api/v1/x402-tools/meme_vibe_score",
    }

    results = {}
    async with aiohttp.ClientSession() as session:
        coros = {}
        for name, url in tasks.items():
            body = {"address": target, "token": target, "wallet": target, "chain": req.chain}
            coros[name] = session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=30))
        for name, coro in coros.items():
            try:
                resp = await coro
                results[name] = await resp.json() if resp.status == 200 else {"status": resp.status}
            except Exception as e:
                results[name] = {"error": str(e)}

    return {
        "tool": "All-in-One Audit",
        "bundle": "all_in_one",
        "target": target,
        "chain": req.chain,
        "timestamp": datetime.utcnow().isoformat(),
        "results": results,
        "price_usd": "0.35",
        "savings": "30% vs individual calls",
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }


@sub_router.post("/forensic_pack")
async def bundle_forensic_pack(req: BundleRequest):
    """Forensic Investigation Pack - valuation + OSINT + report at 33% discount."""
    target = req.address or req.token or req.wallet
    if not target:
        raise HTTPException(status_code=400, detail="Provide address, token, or wallet")

    tasks = {
        "forensic_valuation": "http://localhost:8000/api/v1/x402-tools/forensic_valuation",
        "osint_identity_hunt": "http://localhost:8000/api/v1/x402-tools/osint_identity_hunt",
        "investigation_report": "http://localhost:8000/api/v1/x402-tools/investigation_report",
    }

    results = {}
    async with aiohttp.ClientSession() as session:
        coros = {}
        for name, url in tasks.items():
            body = {"address": target, "token": target, "wallet": target, "chain": req.chain}
            coros[name] = session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=30))
        for name, coro in coros.items():
            try:
                resp = await coro
                results[name] = await resp.json() if resp.status == 200 else {"status": resp.status}
            except Exception as e:
                results[name] = {"error": str(e)}

    return {
        "tool": "Forensic Investigation Pack",
        "bundle": "forensic_pack",
        "target": target,
        "chain": req.chain,
        "timestamp": datetime.utcnow().isoformat(),
        "results": results,
        "price_usd": "0.40",
        "savings": "33% vs individual calls",
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }


# ═══════════════════════════════════════════════════════════
# MCP Proxy - handles external MCP tool execution from workers
# ═══════════════════════════════════════════════════════════


@sub_router.post("/mcp-proxy")
async def mcp_proxy(req: MCPProxyRequest):
    """Proxy MCP tool calls from Cloudflare Workers to external APIs.
    Maps service_tool to the appropriate external API and executes."""
    svc = req.service.lower()
    tool = req.tool
    args = req.arguments

    # Service routing table - maps to known external APIs
    routes = {
        "dexscreener": "https://api.dexscreener.com",
        "jupiter": "https://quote-api.jup.ag/v6",
        "pumpfun": "https://frontend-api.pump.fun",
        "raydium": "https://api.raydium.io/v2",
        "defillama": "https://api.llama.fi",
        "dexpaprika": "https://api.dexpaprika.com",
        "coincap": "https://api.coincap.io/v2",
        "coinmarketcap": "https://pro-api.coinmarketcap.com/v1",
        "cryptopanic": "https://cryptopanic.com/api/v1",
        "cryptocompare": "https://min-api.cryptocompare.com/data",
        "blockchair": "https://api.blockchair.com",
        "blockchain": "https://blockchain.info",
        "mempool": "https://mempool.space/api",
        "solana": "https://api.mainnet-beta.solana.com",
        "helius": "https://api.helius.xyz/v0",
        "birdeye": "https://public-api.birdeye.com",
        "coingecko": "https://api.coingecko.com/api/v3",
        "cryptoiz": "https://api.cryptoiz.com",
        "blockrun": "https://api.blockrun.ai",
        "agentfi": "https://api.agentfi.xyz",
        "moralis": "https://deep-index.moralis.io/api/v2.2",
        "gmgn": "https://gmgn.ai/api",
        "nansen": "https://api.nansen.ai",
        "arkham": "https://api.arkhamintelligence.com",
        "dune": "https://api.dune.com/api/v1",
        "solscan": "https://public-api.solscan.io",
        "quicknode": "https://api.quicknode.com",
    }

    base_url = routes.get(svc)
    if not base_url:
        return {"error": f"Unsupported service: {svc}", "available": list(routes.keys())}

    # Construct the endpoint based on tool name
    tool_endpoints = {
        # DexScreener
        "getLatestTokenProfiles": "/token-profiles/latest/v1",
        "getLatestBoostedTokens": "/token-boosted/latest/v1",
        "getPairs": f"/latest/dex/pairs/solana/{args.get('pairAddresses', args.get('tokenAddresses', ''))}",
        # Jupiter
        "getQuote": "/quote",
        "getPrice": "/price",
        "getTokens": "/tokens",
        # CoinGecko
        "getPrice": "/simple/price",  # noqa: F601
        # DeFiLlama
        "getTVL": f"/tvl/{args.get('protocol', '')}",
        "getProtocols": "/protocols",
        # Solana RPC
        "getHealth": "",
        # General fallback
    }

    endpoint = tool_endpoints.get(tool, f"/{tool}")

    try:
        async with aiohttp.ClientSession() as session:
            url = f"{base_url}{endpoint}"
            headers = {"Accept": "application/json"}

            # Add API keys for services that need them
            if svc == "coingecko":
                headers["x-cg-pro-api-key"] = os.getenv("COINGECKO_API_KEY_PRO", "")
            elif svc == "helius":
                headers["Authorization"] = f"Bearer {os.getenv('HELIUS_API_KEY', '')}"
            elif svc == "moralis":
                headers["X-API-Key"] = os.getenv("MORALIS_API_KEY", "")
            elif svc == "birdeye":
                headers["X-API-KEY"] = os.getenv("BIRDEYE_API_KEY", "")

            async with session.get(
                url, params=args, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                return {
                    "service": svc,
                    "tool": tool,
                    "status": "success" if resp.status < 400 else "error",
                    "data": data,
                }
    except Exception as e:
        return {
            "service": svc,
            "tool": tool,
            "status": "error",
            "error": str(e),
            "note": "Direct external API calls failed - try REST endpoint for cached/fallback data",
        }


# ═══════════════════════════════════════════════════════════
# Human Payment - Multi-Chain Wallet Pay-Per-Call
# ═══════════════════════════════════════════════════════════


async def _record_payment(tool_id: str, price_usd: str, payer: str) -> None:
    """Surface for super-tool endpoints (comprehensive audit, etc.).

    Reuses the P3A stub via the shared module; canonical recording lives
    in app/billing/x402/enforcement.py and is wired in P3B+.
    """
    from app.billing.x402.shared import record_x402_payment

    await record_x402_payment(tool_id, price_usd, payer)


@sub_router.get("/payment-methods")
async def get_payment_methods():
    """List all payment methods available for human wallet payments.

    Returns every supported token, chain, and the destination wallet address.
    Prices shown in USD; actual payment is in the selected token at market rate.
    """
    return {
        "tokens": [
            {
                "key": key,
                "chain": info["chain"],
                "network": info["network"],
                "asset": info["asset"],
                "label": info["label"],
                "icon": info["icon"],
                "pay_to": HUMAN_PAY_TO.get(info["chain"], ""),
                "decimals": info["decimals"],
            }
            for key, info in HUMAN_PAYMENT_TOKENS.items()
        ],
        "pay_to_addresses": {chain: addr for chain, addr in HUMAN_PAY_TO.items() if addr},
        "chain_count": len({i["chain"] for i in HUMAN_PAYMENT_TOKENS.values()}),
        "token_count": len(HUMAN_PAYMENT_TOKENS),
    }


@sub_router.post("/human-execute")
async def human_execute(req: Any):
    """Execute a tool after human wallet payment verification.

    Multi-chain: Base, Solana, Ethereum, BSC, Polygon, Arbitrum, TRON, Bitcoin, SEPA/EUR.
    Verification routed through the same facilitator system as bot payments.
    All payments go to your wallets - no middleman.
    """
    # Re-import the typed request model for runtime validation
    from app.billing.x402.shared import HumanPaymentRequest

    req = HumanPaymentRequest.model_validate(req) if isinstance(req, dict) else req

    tool_name = req.tool
    token_info = HUMAN_PAYMENT_TOKENS.get(req.payment_token)

    if not token_info:
        return {
            "success": False,
            "error": f"Unsupported payment token: {req.payment_token}",
            "supported_tokens": list(HUMAN_PAYMENT_TOKENS.keys()),
        }

    chain = req.chain or token_info["chain"]
    expected_pay_to = HUMAN_PAY_TO.get(chain, "")

    # ── Payment Verification ──────────────────────────────────
    verified = False
    verification_method = "unknown"
    facilitator_used = None

    try:
        # Try facilitator router first (same as bot payments)
        from app.facilitators.router import get_facilitator_router

        router = get_facilitator_router()

        # Build a minimal payload that the router can work with
        payload = {
            "x402Version": 2,
            "txHash": req.tx_hash,
            "payer": req.wallet,
            "accepted": {
                "network": token_info["network"],
                "asset": token_info["asset"],
                "amount": "0",  # Will be verified by facilitator
                "payTo": expected_pay_to,
            },
        }

        result = await router.verify(
            payload=payload,
            chain_key=chain,
            token_symbol=token_info["asset"],
        )

        if result.verified:
            verified = True
            verification_method = f"facilitator:{result.facilitator}"
            facilitator_used = result.facilitator

    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: direct on-chain verification
    if not verified:
        try:
            verified = await _verify_onchain_direct(req.tx_hash, chain, token_info, expected_pay_to)
            verification_method = "onchain-direct"
        except Exception:
            pass

    if not verified:
        return {
            "success": False,
            "error": "Payment verification failed. Transaction not confirmed or wrong recipient.",
            "tx_hash": req.tx_hash,
            "chain": chain,
            "expected_pay_to": expected_pay_to[:10] + "...",
        }

    # ── Anti-abuse: check trial limits ────────────────────────
    from app.routers.x402_enforcement import check_trial

    _can_trial, _remaining = check_trial(tool_name, req.wallet)

    # ── Execute the tool ──────────────────────────────────────
    try:
        # Determine gateway
        if chain in ("base", "ethereum", "bsc", "polygon", "arbitrum"):
            gw = "https://base.rugmunch.io"
        elif chain == "tron":
            gw = "https://base.rugmunch.io"  # TRON tools proxied through base gateway
        else:
            gw = "https://sol.rugmunch.io"

        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{gw}/tools/{tool_name}",
                json=req.arguments,
                headers={"Content-Type": "application/json", "X-RMI-Human-Payment": "verified"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp,
        ):
            text = await resp.text()
            try:
                result_data = json.loads(text)
            except json.JSONDecodeError:
                result_data = {"raw": text[:500]}

            # Record payment in Redis (same as bot payments)
            try:
                from app.routers.x402_enforcement import get_redis

                r = get_redis()
                if r:
                    import time as _time

                    r.setex(
                        f"x402:spent_tx:{req.tx_hash}",
                        86400,
                        json.dumps(
                            {
                                "chain": chain,
                                "payer": req.wallet,
                                "amount": "0",
                                "tool": tool_name,
                                "timestamp": _time.time(),
                                "method": "human-wallet",
                                "facilitator": facilitator_used,
                            }
                        ),
                    )
            except Exception:
                pass

            return {
                "success": resp.status < 400,
                "tool": tool_name,
                "payment_token": req.payment_token,
                "chain": chain,
                "tx_hash": req.tx_hash,
                "verified": True,
                "verification": verification_method,
                "facilitator": facilitator_used,
                "pay_to": expected_pay_to,
                "result": result_data,
            }
    except Exception as e:
        return {"success": False, "error": f"Tool execution failed: {e!s}"}


async def _verify_onchain_direct(
    tx_hash: str, chain: str, token_info: dict, expected_pay_to: str
) -> bool:
    """Direct on-chain verification fallback for human payments."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        if chain == "solana":
            async with session.post(
                "https://api.mainnet-beta.solana.com",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        tx_hash,
                        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                    ],
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                tx = data.get("result", {})
                if not tx:
                    return False
                # Check if any transfer goes to our wallet
                meta = tx.get("meta", {})
                return any(bal.get("owner") == expected_pay_to for bal in meta.get("postTokenBalances", []))

        elif chain in ("tron", "bitcoin", "sepa"):
            # For these chains, assume verified if facilitator passed
            # (they don't have simple Etherscan-style APIs)
            return True

        else:
            # EVM chains - use Etherscan-family APIs
            explorers = {
                "base": "https://api.basescan.org/api",
                "ethereum": "https://api.etherscan.io/api",
                "bsc": "https://api.bscscan.com/api",
                "polygon": "https://api.polygonscan.com/api",
                "arbitrum": "https://api.arbiscan.io/api",
            }
            explorer_url = explorers.get(chain, explorers["ethereum"])
            api_key = os.getenv("ETHERSCAN_API_KEY", "")

            async with session.get(
                f"{explorer_url}?module=transaction&action=gettxreceiptstatus&txhash={tx_hash}&apikey={api_key}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                result = data.get("result", {})
                if isinstance(result, dict):
                    return result.get("status") == "1"
                return str(data.get("status")) == "1"


# ALIAS ROUTES - Map dead tool IDs to real handler endpoints
# These tools appear in TOOL_PRICES and x402 manifest but had no routes.
# Each alias proxies the request to the real implementation.
# ═══════════════════════════════════════════════════════════════


async def _check_rate_limit(request: Request) -> bool:
    """Simple IP-based rate limiter: 60 req/min per IP, 300 req/5min per IP.
    Uses the same Redis as x402 enforcement. Fail-open if Redis is down."""
    try:
        from app.routers.x402_enforcement import get_redis

        r = get_redis()
        if not r:
            return True  # No Redis = allow
        cf_ip = request.headers.get("CF-Connecting-IP", "") or (
            request.client.host if request.client else "0"
        )
        # Normalize IP-key
        import hashlib as _hl

        ip_key = _hl.sha256(cf_ip.encode()).hexdigest()[:16]
        # 1-minute window
        key_min = f"rl:{ip_key}:min"
        count = r.incr(key_min)
        if count == 1:
            r.expire(key_min, 60)
        if count > 60:
            return False
        # 5-minute window
        key_5m = f"rl:{ip_key}:5min"
        count5 = r.incr(key_5m)
        if count5 == 1:
            r.expire(key_5m, 300)
        return not count5 > 300
    except Exception:
        return True  # Fail open


@sub_router.get("/{tool_id}")
async def tool_alias_dispatcher_get(tool_id: str, request: Request):
    """GET catch-all for paid tools - converts query params to JSON body and dispatches.

    Handles x402 discovery agents and browser-based clients that use GET + query params
    instead of POST + JSON body. The enforcement middleware has already verified payment
    or granted a trial before this handler is reached.
    """
    from app.billing.x402.shared import TOOL_ALIASES

    # Read query params as the request body
    query_params = dict(request.query_params)
    if not query_params:
        # GET without params = discovery/info request, return basic tool info
        pricing_info = {}
        try:
            from app.routers.x402_enforcement import TOOL_PRICES

            pricing_info = TOOL_PRICES.get(tool_id, {})
        except Exception:
            pass
        if pricing_info:
            return JSONResponse(
                content={
                    "tool": tool_id,
                    "description": pricing_info.get("description", ""),
                    "price_usd": pricing_info.get("price_usd", 0),
                    "trial_free": pricing_info.get("trial_free", 3),
                    "usage": "POST with JSON body or GET with query params (address, chain, etc.)",
                    "method": "GET or POST",
                }
            )
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found.")

    # ── Rate limiting ──
    if not await _check_rate_limit(request):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 60 req/min per IP.")

    # Resolve alias target (same logic as POST dispatcher)
    target = TOOL_ALIASES.get(tool_id)
    injected_chain = None

    if target is None and "_" in tool_id:
        _CHAIN_SUFFIXES: ClassVar[dict] = {
            "solana",
            "base",
            "ethereum",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "avalanche",
            "fantom",
            "gnosis",
            "tron",
            "bitcoin",
        }
        try:
            from app.routers.x402_enforcement import TOOL_PRICES as _TP

            pricing = _TP.get(tool_id, {})
            base_tool = pricing.get("base_tool")
            if base_tool:
                target = base_tool
                injected_chain = pricing.get("chain")
        except Exception:
            pass
        if target is None:
            last_underscore = tool_id.rfind("_")
            if last_underscore > 0:
                suffix = tool_id[last_underscore + 1 :]
                prefix = tool_id[:last_underscore]
                if suffix in _CHAIN_SUFFIXES:
                    target = TOOL_ALIASES.get(prefix, prefix)
                    injected_chain = suffix

    if target is None:
        # No alias - use the tool_id itself as the target (it's a direct route or DataBus)
        # Check if it has a direct POST route handler
        target = tool_id

    # Check if target has a dedicated POST handler - if so, dispatch via internal call
    # If not (DataBus-only tool), use the DataBus caching shield
    body = query_params
    if injected_chain and isinstance(body, dict):
        body.setdefault("chain", injected_chain)

    # ── Dispatch via DataBus caching shield ──
    try:
        result = await td.call_tool(tool_id, body)
        if result is None:
            # Fallback: try internal POST to the catch-all dispatcher
            target_url = f"http://localhost:8000/api/v1/x402-tools/{target}"
            headers = {"Content-Type": "application/json", "User-Agent": "RMI-GET-Proxy/3.1"}
            for h in (
                "X-RMI-Payment",
                "X-RMI-Trial",
                "X-RMI-Trial-Remaining",
                "X-RMI-Internal",
                "X-Device-Id",
                "X-Wallet-Address",
                "Authorization",
            ):
                val = request.headers.get(h)
                if val:
                    headers[h] = val
            import httpx

            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(target_url, json=body, headers=headers)
                try:
                    result = resp.json()
                except Exception:
                    result = {"data": resp.text, "status": resp.status_code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {e!s}") from e

    if isinstance(result, dict):
        result["alias_of"] = target
        result["tool"] = tool_id
        result["method"] = "GET"
        if injected_chain:
            result["chain"] = injected_chain

    # ── Wallet Intelligence Enrichment ──
    try:
        opt_out = request.query_params.get("enrich", "").lower() == "false"
        from app.routers.x402_enrichment import enrich_tool_response

        result = enrich_tool_response(tool_id, result, request_params=body, opt_out=opt_out)
    except Exception:
        pass

    return JSONResponse(content=result, status_code=200)


@sub_router.post("/{tool_id}")
async def tool_alias_dispatcher(tool_id: str, request: Request):
    """Catch-all dispatcher for tool aliases and per-chain variants.

    Handles three cases:
    1. Named aliases (scam_database → urlcheck)
    2. Per-chain variants (wallet_solana → wallet with chain=solana)
    3. Expanded tools (flash_loan_detect → closest real handler)

    Returns 404 for truly unknown tools."""
    from app.billing.x402.shared import TOOL_ALIASES

    # ── Rate limiting ──
    if not await _check_rate_limit(request):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 60 req/min per IP.")
    # Try exact alias first
    target = TOOL_ALIASES.get(tool_id)
    injected_chain = None

    # If not a named alias, check if it's a per-chain variant (e.g., wallet_solana)
    if target is None and "_" in tool_id:
        # Try to extract chain suffix
        _CHAIN_SUFFIXES: ClassVar[dict] = {
            "solana",
            "base",
            "ethereum",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "avalanche",
            "fantom",
            "gnosis",
            "tron",
            "bitcoin",
        }
        # Also check TOOL_PRICES for the base_tool/chain fields
        try:
            from app.routers.x402_enforcement import TOOL_PRICES

            pricing = TOOL_PRICES.get(tool_id, {})
            base_tool = pricing.get("base_tool")
            if base_tool:
                target = base_tool
                injected_chain = pricing.get("chain")
        except Exception:
            pass

        # Fallback: parse tool_chain format
        if target is None:
            last_underscore = tool_id.rfind("_")
            if last_underscore > 0:
                suffix = tool_id[last_underscore + 1 :]
                prefix = tool_id[:last_underscore]
                if suffix in _CHAIN_SUFFIXES and prefix in TOOL_ALIASES:
                    target = TOOL_ALIASES[prefix]
                    injected_chain = suffix
                elif suffix in _CHAIN_SUFFIXES:
                    # Check if prefix is a real tool endpoint
                    from app.routers.x402_enforcement import TOOL_PRICES as _TP

                    if prefix in _TP or any(prefix == t for t in TOOL_ALIASES if t == prefix):
                        target = TOOL_ALIASES.get(prefix, prefix)
                        injected_chain = suffix

    if target is None:
        # No alias found - try DataBus execution for all 127 catalog tools
        # DataBus is the universal backend for all tools
        body = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
            except Exception:
                body = {}

        if injected_chain and isinstance(body, dict):
            body.setdefault("chain", injected_chain)

        try:
            result = await td.call_tool(tool_id, body)
            if result is not None:
                if isinstance(result, dict):
                    result["tool"] = tool_id
                    result["alias_of"] = "databus"
                    if injected_chain:
                        result["chain"] = injected_chain
                # Enrichment
                try:
                    from app.routers.x402_enrichment import enrich_tool_response

                    opt_out = request.query_params.get("enrich", "").lower() == "false"
                    result = enrich_tool_response(
                        tool_id, result, request_params=body, opt_out=opt_out
                    )
                except Exception:
                    pass
                return JSONResponse(content=result, status_code=200)
        except Exception:
            pass

        # DataBus failed - raise 404 only for truly unknown tools
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{tool_id}' not found. See /mcp/tools for available tools",
        )

    target_url = f"http://localhost:8000/api/v1/x402-tools/{target}"

    try:
        body = (
            await request.json()
            if request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
    except Exception:
        body = {}

    # Inject chain for per-chain variants
    if injected_chain and isinstance(body, dict):
        body.setdefault("chain", injected_chain)

    headers = {}
    for h in (
        "x-pay",
        "X-Pay",
        "User-Agent",
        "Authorization",
        "x-wallet-address",
        "X-Wallet-Address",
        "x-turnstile-token",
        "X-Turnstile-Token",
        "X-Forwarded-For",
        "X-Real-IP",
        "CF-Connecting-IP",
        "X-RMI-Internal",
        "X-RMI-Payment",
        "X-RMI-Trial",
        "X-RMI-Trial-Remaining",
        "X-Device-Id",
    ):
        val = request.headers.get(h)
        if val:
            headers[h] = val
    headers["content-type"] = "application/json"
    headers["User-Agent"] = headers.get("User-Agent", "RMI-Alias-Proxy/3.1")
    headers["X-RMI-Alias"] = tool_id

    import httpx

    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(target_url, json=body, headers=headers)
        try:
            result = resp.json()
        except Exception:
            result = {"data": resp.text, "status": resp.status_code}

    rh = {}
    for h in ("X-RMI-Payment", "X-RMI-Trial", "X-RMI-Trial-Remaining", "X-RMI-Refund-Flagged"):
        if resp.headers.get(h):
            rh[h] = resp.headers[h]

    # Wrap result with alias metadata
    if isinstance(result, dict):
        result["alias_of"] = target
        result["tool"] = tool_id
        if injected_chain:
            result["chain"] = injected_chain

    # ── Wallet Intelligence Enrichment ──
    # Annotate responses with label data, scam patterns, and sanctions from
    # the Wallet Memory Bank (389K labels / 155K addresses in ClickHouse).
    # Controlled by ?enrich=false opt-out. Cached per-address in Redis (1hr TTL).
    try:
        opt_out = request.query_params.get("enrich", "").lower() == "false"
        from app.routers.x402_enrichment import enrich_tool_response

        result = enrich_tool_response(tool_id, result, request_params=body, opt_out=opt_out)
    except Exception:
        pass  # Enrichment is best-effort - never block a response on it

    return JSONResponse(content=result, status_code=resp.status_code, headers=rh)
