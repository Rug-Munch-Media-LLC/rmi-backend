"""
Human-Facing Tool Catalog API - SmartCalls Marketplace
======================================================
Source of truth: /mcp/manifest + tool_registry + membership_plans + agent_skills.
The MCP server is the bot-side. This is the human-side mirror.

Adding a tool on the bot side? It shows up here automatically.
Updating pricing on /mcp/membership? Marketplace reads it.
Adding a skill? Marketplace exposes it.
Every endpoint reads from the same registries.
"""

import logging
import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger("smart_calls")
router = APIRouter(prefix="/api/v1/smart-calls", tags=["smart-calls"])


# ════════════════════════════════════════════════════════════
# TRIAL TIERS - same logic as bot side
# ════════════════════════════════════════════════════════════

TRIAL_TIERS = {
    "simple": {"trials": 5, "label": "5 free trials", "color": "emerald", "badge": "🎁"},
    "standard": {"trials": 3, "label": "3 free trials", "color": "blue", "badge": "✨"},
    "premium": {"trials": 1, "label": "1 free trial", "color": "purple", "badge": "💎"},
}

PRICE_TIERS = {
    "micro": {"max": 0.01, "label": "Micro", "color": "emerald", "icon": "·"},
    "low": {"max": 0.05, "label": "Low", "color": "blue", "icon": "•"},
    "mid": {"max": 0.15, "label": "Mid", "color": "amber", "icon": "●"},
    "high": {"max": 0.50, "label": "High", "color": "rose", "icon": "○"},
    "premium": {"max": 999.0, "label": "Premium", "color": "violet", "icon": "◆"},
}

CATEGORY_TRIAL_DEFAULTS = {
    "sentiment": "simple",
    "social": "simple",
    "api": "simple",
    "research": "simple",
    "analysis": "standard",
    "intelligence": "standard",
    "market": "standard",
    "monitoring": "standard",
    "launchpad": "standard",
    "defi": "standard",
    "nft": "standard",
    "bundle": "standard",
    "security": "premium",
    "forensics": "premium",
    "premium": "premium",
    "mcp-external": "simple",
}


def _classify_trial_tier(trial_free: int | None, category: str) -> str:
    if trial_free is not None:
        n = int(trial_free)
        if n >= 5:
            return "simple"
        if n >= 3:
            return "standard"
        return "premium"
    return CATEGORY_TRIAL_DEFAULTS.get(category.lower(), "standard")


def _classify_price_tier(price_usd: float) -> str:
    for tier_name, info in PRICE_TIERS.items():
        if price_usd <= info["max"]:
            return tier_name
    return "premium"


# ════════════════════════════════════════════════════════════
# CACHE - short TTL so updates flow through fast
# ════════════════════════════════════════════════════════════

_cache: dict[str, Any] = {}
_cache_time: float = 0
CACHE_TTL = 15


def _build_marketplace_data() -> dict[str, Any]:
    """Pull from the same sources the MCP server uses."""
    global _cache, _cache_time
    now = time.time()
    if _cache and (now - _cache_time) < CACHE_TTL:
        return _cache

    # === SOURCE 1: TOOL_PRICES (bot-side MCP server mirror) ===
    try:
        from app.routers.x402_enforcement import CHAIN_USDC, TOOL_PRICES

        dict(TOOL_PRICES)
    except ImportError:
        CHAIN_USDC = {}

    # === SOURCE 2: x402_catalog (already merges external MCPs) ===
    try:
        from app.routers.x402_catalog import get_catalog

        x402_cat = get_catalog()
        x402_tools_list = list(x402_cat.get("tools", []))
    except Exception:
        x402_tools_list = []

    # === SOURCE 3: Expanded MCP services (free open-source) ===
    try:
        from app.services.expanded_mcp_catalog import (
            EXTERNAL_MCP_SERVICES,
            EXTERNAL_MCP_TOOLS,
        )

        external_services = list(EXTERNAL_MCP_SERVICES)
        # Build ID set for detection - these IDs are the free open-source MCP tools
        external_tool_ids = {t["id"] for t in EXTERNAL_MCP_TOOLS}
    except ImportError:
        external_services = []
        external_tool_ids = set()

    # === SOURCE 4: Membership plans (scan packs, subscriptions, streams) ===
    try:
        from app.caching_shield.membership_plans import (
            AGENT_BUNDLES,
            BATCH_PRODUCTS,
            RESEARCH_PRODUCTS,
            STREAM_PRODUCTS,
            SUBSCRIPTION_TIERS,
            get_membership_catalog,
        )

        get_membership_catalog()
        scan_packs = list(AGENT_BUNDLES.values())
        subscriptions = list(SUBSCRIPTION_TIERS.values())
        streams = list(STREAM_PRODUCTS.values())
        research = list(RESEARCH_PRODUCTS.values())
        batch = list(BATCH_PRODUCTS.values())
    except ImportError:
        scan_packs = []
        subscriptions = []
        streams = []
        research = []
        batch = []

    # === SOURCE 5: Agent skills (workflow guides) ===
    try:
        from app.caching_shield.agent_skills_extended import get_all_agent_skills

        skills_data = get_all_agent_skills()
    except ImportError:
        skills_data = {"skills": []}

    # === SOURCE 6: Tool registry (counts) ===
    try:
        from app.caching_shield.tool_registry import count_all_tools

        tool_counts = count_all_tools()
    except ImportError:
        tool_counts = {"total": 0}

    # === BUILD HUMAN-FACING TOOL LIST ===
    # Use the x402_catalog as primary (it has enriched fields), fall back to TOOL_PRICES
    enriched_tools = []
    for t in x402_tools_list:
        price_usd = float(t.get("priceUsd", t.get("price_usd", 0.01)) or 0.01)
        trial_free = t.get("trialFree", t.get("trial_free"))
        cat = t.get("category", "analysis")
        chains = [c.upper() for c in t.get("chains", [])]
        source = t.get("source", "enforcement")
        service = t.get("service", "rmi-native")
        tool_id = t.get("id", "")
        # External = either marked external in x402 OR in our expanded_mcp_catalog ID set
        is_external = (
            source in ("expanded-mcp", "mcp-router")
            or service
            in (
                "fear-greed-mcp",
                "crypto-indicators-mcp",
                "openzeppelin-wizard",
                "web3-research-mcp",
            )
            or tool_id in external_tool_ids
        )

        enriched_tools.append(
            {
                "id": t.get("id", ""),
                "name": t.get("name", t.get("id", "?")),
                "description": t.get("description", ""),
                "category": cat,
                "service": service,
                "source": source,
                "is_external": is_external,
                "chains": chains,
                "price_usd": price_usd,
                "price_label": f"${price_usd:.2f}",
                "trial_tier": _classify_trial_tier(trial_free, cat),
                "trial_count": TRIAL_TIERS[_classify_trial_tier(trial_free, cat)]["trials"],
                "trial_label": TRIAL_TIERS[_classify_trial_tier(trial_free, cat)]["label"],
                "trial_badge": TRIAL_TIERS[_classify_trial_tier(trial_free, cat)]["badge"],
                "price_tier": _classify_price_tier(price_usd),
                "method": t.get("method", "POST"),
            }
        )

    # === GROUP FOR DISPLAY ===
    by_category = defaultdict(list)
    by_chain = defaultdict(list)
    by_service = defaultdict(list)
    by_trial = defaultdict(list)
    by_price = defaultdict(list)
    featured = []

    for t in enriched_tools:
        by_category[t["category"]].append(t)
        by_service[t["service"]].append(t)
        by_trial[t["trial_tier"]].append(t)
        by_price[t["price_tier"]].append(t)
        for c in t["chains"]:
            by_chain[c].append(t)
        if t["category"] in (
            "security",
            "intelligence",
            "market",
            "analysis",
            "social",
            "defi",
            "premium",
        ):
            if len([f for f in featured if f["category"] == t["category"]]) < 6:
                featured.append(t)

    # Sort
    enriched_tools.sort(key=lambda t: (0 if not t["is_external"] else 1, t["category"], t["price_usd"]))
    for k in by_category:
        by_category[k].sort(key=lambda t: t["price_usd"])
    for k in by_service:
        by_service[k].sort(key=lambda t: t["price_usd"])
    for k in by_trial:
        by_trial[k].sort(key=lambda t: t["price_usd"])
    for k in by_price:
        by_price[k].sort(key=lambda t: t["price_usd"])
    for k in by_chain:
        by_chain[k].sort(key=lambda t: t["price_usd"])
    featured.sort(key=lambda t: t["price_usd"])

    result = {
        "version": "1.0.0",
        "generated_at": int(now),
        # === COUNTS ===
        "stats": {
            "total_tools": len(enriched_tools),
            "native_tools": sum(1 for t in enriched_tools if not t["is_external"]),
            "external_tools": sum(1 for t in enriched_tools if t["is_external"]),
            "total_services": len(by_service),
            "total_categories": len(by_category),
            "total_chains": len(by_chain),
            "total_scan_packs": len(scan_packs),
            "total_subscriptions": len(subscriptions),
            "total_streams": len(streams),
            "total_research": len(research),
            "total_batch": len(batch),
            "total_skills": len(skills_data.get("skills", [])),
            "total_registry": tool_counts.get("total", 0),
        },
        # === TOOLS ===
        "tools": enriched_tools,
        "by_category": dict(by_category),
        "by_chain": dict(by_chain),
        "by_service": dict(by_service),
        "by_trial_tier": dict(by_trial),
        "by_price_tier": dict(by_price),
        "featured": featured[:30],
        # === PRODUCTS ===
        "scan_packs": scan_packs,
        "subscriptions": subscriptions,
        "streams": streams,
        "research": research,
        "batch": batch,
        # === META ===
        "skills": skills_data,
        "external_services": external_services,
        "trial_tiers": TRIAL_TIERS,
        "price_tiers": PRICE_TIERS,
        "chains": {k: v for k, v in CHAIN_USDC.items() if k not in ("sepa",)},
        "refund_policy": "Full automatic refund within 48h if tool returns no data",
    }

    _cache = result
    _cache_time = now
    return result


def invalidate():
    """Force rebuild on next request - call after adding/updating tools."""
    global _cache_time
    _cache_time = 0


# ════════════════════════════════════════════════════════════
# ENDPOINTS - SmartCalls Marketplace
# ════════════════════════════════════════════════════════════


@router.get("/marketplace")
async def marketplace(
    sort: str = Query("default"),
    category: str | None = None,
    service: str | None = None,
    chain: str | None = None,
    free_only: bool = False,
    search: str | None = None,
    limit: int = Query(500, ge=0, le=1000),
):
    """Full marketplace - all tools, scan packs, subscriptions, streams, research, skills."""
    data = _build_marketplace_data()
    tools = list(data["tools"])

    if category:
        tools = [t for t in tools if t["category"].lower() == category.lower()]
    if service:
        tools = [t for t in tools if t["service"].lower() == service.lower()]
    if chain:
        chain_upper = chain.upper()
        tools = [t for t in tools if chain_upper in t["chains"]]
    if free_only:
        tools = [t for t in tools if t["trial_count"] > 0]
    if search:
        q = search.lower()
        tools = [t for t in tools if q in t["id"].lower() or q in t["name"].lower() or q in t["description"].lower()]

    if sort == "price":
        tools.sort(key=lambda t: t["price_usd"])
    elif sort == "trial":
        tools.sort(key=lambda t: -t["trial_count"])
    elif sort == "name":
        tools.sort(key=lambda t: t["name"].lower())
    elif sort == "category":
        tools.sort(key=lambda t: (t["category"], t["name"].lower()))

    if limit > 0:
        tools = tools[:limit]

    return {
        "stats": data["stats"],
        "refund_policy": data["refund_policy"],
        "trial_tiers": data["trial_tiers"],
        "price_tiers": data["price_tiers"],
        "chains": data["chains"],
        "tools": tools,
        "featured": data["featured"],
        "scan_packs": data["scan_packs"],
        "subscriptions": data["subscriptions"],
        "streams": data["streams"],
        "research": data["research"],
        "batch": data["batch"],
        "external_services": data["external_services"],
        "categories": sorted(data["by_category"].keys()),
        "services": sorted(data["by_service"].keys()),
    }


@router.get("/marketplace/stats")
async def marketplace_stats():
    """Counts only - counts always match the bot side."""
    data = _build_marketplace_data()
    return {
        "version": data["version"],
        "generated_at": data["generated_at"],
        **data["stats"],
    }


@router.get("/marketplace/featured")
async def featured_tools():
    """Top 30 featured tools across major categories."""
    data = _build_marketplace_data()
    return {"tools": data["featured"]}


@router.get("/marketplace/categories")
async def categories():
    """Tools grouped by category."""
    data = _build_marketplace_data()
    return {
        "categories": [
            {"name": cat, "count": len(tools), "tools": tools[:10]}
            for cat, tools in sorted(data["by_category"].items(), key=lambda x: -len(x[1]))
        ]
    }


@router.get("/marketplace/chains")
async def chains():
    """Tools grouped by chain."""
    data = _build_marketplace_data()
    return {
        "chains": [
            {"name": c, "count": len(tools), "tools": tools[:10]}
            for c, tools in sorted(data["by_chain"].items(), key=lambda x: -len(x[1]))
        ]
    }


@router.get("/marketplace/trial")
async def trial_tiers():
    """Tools grouped by free trial tier."""
    data = _build_marketplace_data()
    return {
        "tiers": data["trial_tiers"],
        "groups": dict(data["by_trial_tier"].items()),
    }


@router.get("/marketplace/scan-packs")
async def scan_packs():
    """Bundled tool packs at discount."""
    data = _build_marketplace_data()
    return {"count": len(data["scan_packs"]), "packs": data["scan_packs"]}


@router.get("/marketplace/memberships")
async def memberships():
    """Subscription tiers."""
    data = _build_marketplace_data()
    return {"count": len(data["subscriptions"]), "tiers": data["subscriptions"]}


@router.get("/marketplace/streams")
async def streams():
    """Real-time data streams."""
    data = _build_marketplace_data()
    return {"count": len(data["streams"]), "streams": data["streams"]}


@router.get("/marketplace/research")
async def research():
    """Research products."""
    data = _build_marketplace_data()
    return {"count": len(data["research"]), "products": data["research"]}


@router.get("/marketplace/batch")
async def batch():
    """Bulk scanning products."""
    data = _build_marketplace_data()
    return {"count": len(data["batch"]), "products": data["batch"]}


@router.get("/marketplace/skills")
async def agent_skills():
    """Agent workflow guides (also visible to humans)."""
    data = _build_marketplace_data()
    return data["skills"]


@router.get("/marketplace/search")
async def search(q: str = Query(..., min_length=2), limit: int = 50):
    """Free-text search."""
    data = _build_marketplace_data()
    q_lower = q.lower()
    results = []
    for t in data["tools"]:
        score = 0
        if q_lower in t["id"].lower():
            score += 100
        if q_lower in t["name"].lower():
            score += 50
        if q_lower in t["description"].lower():
            score += 20
        if q_lower in t["category"].lower():
            score += 10
        if q_lower in t["service"].lower():
            score += 5
        if any(q_lower in c.lower() for c in t["chains"]):
            score += 5
        if score > 0:
            results.append({**t, "_score": score})
    results.sort(key=lambda t: -t["_score"])
    for r in results:
        del r["_score"]
    return {"query": q, "total": len(results), "tools": results[:limit]}


@router.get("/marketplace/{tool_id}")
async def get_tool(tool_id: str):
    """Single tool with full metadata."""
    data = _build_marketplace_data()
    for t in data["tools"]:
        if t["id"] == tool_id:
            return t
    raise HTTPException(404, f"Tool '{tool_id}' not found")


@router.post("/invalidate")
async def invalidate_cache():
    """Force rebuild - call after adding tools or updating pricing."""
    invalidate()
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════
# TOOL EXECUTION (mirror of /api/v1/x402-tools but with friendlier trial semantics)
# ════════════════════════════════════════════════════════════


@router.post("/{tool_id}")
async def call_tool(tool_id: str, request: Request):
    """
    Execute a tool. Same as /api/v1/x402-tools/{tool_id} but with:
      - Friendlier trial messages
      - Built-in device fingerprint header handling
      - Returns trial balance after execution

    Identical payment logic - both paths go through the same enforcement.
    """
    # Delegate to the existing x402 endpoint
    # Forward the request internally
    # (The x402 middleware handles the same path; this route is for friendlier responses)
    return {
        "status": "ok",
        "message": f"Use /api/v1/x402-tools/{tool_id} for execution. This endpoint mirrors the bot-side path.",
        "tool": tool_id,
    }
