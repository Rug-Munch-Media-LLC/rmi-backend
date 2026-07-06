"""
Human-Facing Tool Catalog API
================================
Rich, groupable, searchable catalog for the public web terminal and Telegram bot.
Every tool from TOOL_PRICES + expanded_mcp_catalog is auto-exposed here.
Adding a tool anywhere in the system? It's automatically picked up here.

Endpoints:
  GET /api/v1/catalog                  - Full catalog grouped by category + service
  GET /api/v1/catalog/search?q=        - Free-text search
  GET /api/v1/catalog/featured          - Curated featured tools (5 per category)
  GET /api/v1/catalog/by-trial          - Grouped by trial count
  GET /api/v1/catalog/by-price          - Grouped by price tier (free/$0.01/$0.05/etc)
  GET /api/v1/catalog/by-chain/{chain}  - Tools for a specific chain
  GET /api/v1/catalog/{tool_id}         - Single tool with full metadata
  GET /api/v1/catalog/stats             - Catalog statistics

Trial policy tiers (per tool):
  simple: 5 free trials (sentiment, info, lookup)
  standard: 3 free trials (analysis, security scan)
  premium: 1 free trial (audit, deep scan, codegen)
"""

import logging
import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("human_catalog")
router = APIRouter(prefix="/api/v1/catalog", tags=["human-catalog"])

# ════════════════════════════════════════════════════════════
# Trial tier mapping
# ════════════════════════════════════════════════════════════

# Tiers based on tool value/computational cost
TRIAL_TIERS = {
    "free": {"trials": 5, "label": "Free", "color": "emerald", "badge": "🆓"},
    "simple": {"trials": 5, "label": "5 free trials", "color": "emerald", "badge": "🎁"},
    "standard": {"trials": 3, "label": "3 free trials", "color": "blue", "badge": "✨"},
    "premium": {"trials": 1, "label": "1 free trial", "color": "purple", "badge": "💎"},
}

PRICE_TIERS = {
    "micro": {"max": 0.01, "label": "Micro", "color": "emerald"},
    "low": {"max": 0.05, "label": "Low", "color": "blue"},
    "mid": {"max": 0.15, "label": "Mid", "color": "amber"},
    "high": {"max": 0.50, "label": "High", "color": "rose"},
    "premium": {"max": 999.0, "label": "Premium", "color": "violet"},
}

# Categories and their complexity (determines trial count)
CATEGORY_TRIAL_DEFAULTS = {
    # Simple lookup/info - high trial count
    "sentiment": "simple",
    "social": "simple",
    "api": "simple",
    "research": "simple",
    # Standard analysis - medium trial count
    "analysis": "standard",
    "intelligence": "standard",
    "market": "standard",
    "monitoring": "standard",
    # Premium/heavy - low trial count
    "security": "premium",
    "forensics": "premium",
    "premium": "premium",
    "launchpad": "standard",
    "defi": "standard",
    "nft": "standard",
    "bundle": "standard",
    "mcp-external": "simple",
}


def _classify_trial_tier(tool: dict) -> str:
    """Determine trial tier from tool metadata."""
    # Honor explicit trialFree in price_usd range
    if tool.get("trialFree") is not None:
        n = int(tool["trialFree"])
        if n >= 5:
            return "simple"
        elif n >= 3:
            return "standard"
        else:
            return "premium"

    # Fall back to category default
    cat = tool.get("category", "analysis").lower()
    return CATEGORY_TRIAL_DEFAULTS.get(cat, "standard")


def _classify_price_tier(price_usd: float) -> str:
    """Determine price tier from USD amount."""
    for tier_name, tier_info in PRICE_TIERS.items():
        if price_usd <= tier_info["max"]:
            return tier_name
    return "premium"


# ════════════════════════════════════════════════════════════
# Cache - rebuild catalog from sources on demand
# ════════════════════════════════════════════════════════════

_catalog_cache: dict[str, Any] = {}
_catalog_cache_time: float = 0
CATALOG_CACHE_TTL = 30  # 30 seconds - short enough to pick up new tools quickly


def _get_full_catalog() -> dict[str, Any]:
    """Pull the live catalog from all sources. Cached for CATALOG_CACHE_TTL."""
    global _catalog_cache, _catalog_cache_time

    now = time.time()
    if _catalog_cache and (now - _catalog_cache_time) < CATALOG_CACHE_TTL:
        return _catalog_cache

    # Source 1: x402 enforcement (TOOL_PRICES) - authoritative for our 221 native tools
    try:
        from app.routers.x402_enforcement import TOOL_PRICES

        dict(TOOL_PRICES)
    except ImportError:
        pass

    # Source 2: x402_catalog (which merges everything)
    try:
        from app.routers.x402_catalog import get_catalog

        get_catalog_func = get_catalog
    except ImportError:
        get_catalog_func = None

    # Source 3: expanded_mcp_catalog (the 25 new tools)
    try:
        from app.services.expanded_mcp_catalog import EXTERNAL_MCP_SERVICES, EXTERNAL_MCP_TOOLS

        {t["id"] for t in EXTERNAL_MCP_TOOLS}
        external_services = list(EXTERNAL_MCP_SERVICES)
    except ImportError:
        external_services = []

    # Combine: take the x402_catalog full output and enrich
    if get_catalog_func:
        try:
            merged = get_catalog_func()
            tools_list = list(merged.get("tools", []))
        except Exception:
            tools_list = []
    else:
        tools_list = []

    # Convert each to human-friendly format
    enriched = []
    for t in tools_list:
        price_usd = float(t.get("priceUsd", 0.01) or 0.01)
        trial_tier = _classify_trial_tier(t)
        price_tier = _classify_price_tier(price_usd)

        enriched.append(
            {
                "id": t.get("id", ""),
                "name": t.get("name", t.get("id", "?")),
                "description": t.get("description", ""),
                "category": t.get("category", "analysis"),
                "service": t.get("service", "rmi-native"),
                "source": t.get("source", "rmi-native"),
                "chains": t.get("chains", []),
                "price_usd": price_usd,
                "price_label": f"${price_usd:.2f}",
                "trial_tier": trial_tier,
                "trial_count": TRIAL_TIERS[trial_tier]["trials"],
                "trial_label": TRIAL_TIERS[trial_tier]["label"],
                "trial_badge": TRIAL_TIERS[trial_tier]["badge"],
                "price_tier": price_tier,
                "price_tier_label": PRICE_TIERS[price_tier]["label"],
                "method": t.get("method", "POST"),
            }
        )

    # Group by category
    by_category = defaultdict(list)
    for t in enriched:
        by_category[t["category"]].append(t)

    # Group by service
    by_service = defaultdict(list)
    for t in enriched:
        by_service[t["service"]].append(t)

    # Group by trial tier
    by_trial = defaultdict(list)
    for t in enriched:
        by_trial[t["trial_tier"]].append(t)

    # Group by price tier
    by_price = defaultdict(list)
    for t in enriched:
        by_price[t["price_tier"]].append(t)

    # Build featured set - top 5 from each major category
    major_cats = ["security", "intelligence", "market", "analysis", "social", "defi"]
    featured = []
    for cat in major_cats:
        cat_tools = by_category.get(cat, [])
        featured.extend(cat_tools[:5])

    # Sort: native first, then external
    enriched.sort(key=lambda t: (0 if t["source"] == "enforcement" else 1, t["price_usd"]))

    result = {
        "version": "1.0.0",
        "generated_at": int(now),
        "total_tools": len(enriched),
        "total_native": sum(1 for t in enriched if t["source"] in ("enforcement", "route", "rmi-native")),
        "total_external": sum(1 for t in enriched if t["source"] in ("expanded-mcp", "mcp-router")),
        "total_services": len(by_service),
        "total_categories": len(by_category),
        "total_chains": len({c for t in enriched for c in t["chains"]}),
        "categories": sorted(by_category.keys()),
        "services": sorted(by_service.keys()),
        "tools": enriched,
        "by_category": dict(by_category.items()),
        "by_service": dict(by_service.items()),
        "by_trial_tier": dict(by_trial.items()),
        "by_price_tier": dict(by_price.items()),
        "featured": featured,
        "external_services": external_services,
        "trial_tiers": TRIAL_TIERS,
        "price_tiers": PRICE_TIERS,
    }

    _catalog_cache = result
    _catalog_cache_time = now
    return result


def invalidate_catalog_cache():
    """Force rebuild on next request - call after adding/updating tools."""
    global _catalog_cache_time
    _catalog_cache_time = 0


# ════════════════════════════════════════════════════════════
# Endpoints
# ════════════════════════════════════════════════════════════


@router.get("")
async def get_catalog_root(
    sort: str = Query("default", description="default, price, trial, name"),
    category: str | None = Query(None, description="Filter by category"),
    service: str | None = Query(None, description="Filter by service"),
    chain: str | None = Query(None, description="Filter by chain"),
    free_only: bool = Query(False, description="Only tools with free trials"),
    limit: int = Query(500, description="Max tools to return (0 = all)"),
):
    """Full human-facing catalog. Grouped + filterable + sortable."""
    cat = _get_full_catalog()
    tools = list(cat["tools"])

    # Filters
    if category:
        tools = [t for t in tools if t["category"].lower() == category.lower()]
    if service:
        tools = [t for t in tools if t["service"].lower() == service.lower()]
    if chain:
        chain_upper = chain.upper()
        tools = [t for t in tools if chain_upper in [c.upper() for c in t["chains"]]]
    if free_only:
        tools = [t for t in tools if t["trial_count"] > 0]

    # Sort
    if sort == "price":
        tools.sort(key=lambda t: t["price_usd"])
    elif sort == "trial":
        tools.sort(key=lambda t: -t["trial_count"])
    elif sort == "name":
        tools.sort(key=lambda t: t["name"].lower())
    elif sort == "category":
        tools.sort(key=lambda t: (t["category"], t["name"].lower()))

    # Limit
    if limit > 0:
        tools = tools[:limit]

    return {
        "total": len(tools),
        "total_available": cat["total_tools"],
        "version": cat["version"],
        "generated_at": cat["generated_at"],
        "filters": {
            "category": category,
            "service": service,
            "chain": chain,
            "sort": sort,
            "free_only": free_only,
        },
        "tools": tools,
        "categories": cat["categories"],
        "services": cat["services"],
        "trial_tiers": cat["trial_tiers"],
        "price_tiers": cat["price_tiers"],
    }


@router.get("/search")
async def search_catalog(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(50, description="Max results"),
):
    """Free-text search across tool names, descriptions, IDs, and chains."""
    cat = _get_full_catalog()
    q_lower = q.lower()
    results = []

    for t in cat["tools"]:
        score = 0
        # ID match (highest)
        if q_lower in t["id"].lower():
            score += 100
        # Name match
        if q_lower in t["name"].lower():
            score += 50
        # Description match
        if q_lower in t["description"].lower():
            score += 20
        # Category match
        if q_lower in t["category"].lower():
            score += 10
        # Service match
        if q_lower in t["service"].lower():
            score += 5
        # Chain match
        if any(q_lower in c.lower() for c in t["chains"]):
            score += 5

        if score > 0:
            results.append({**t, "_score": score})

    # Sort by score
    results.sort(key=lambda t: -t["_score"])
    # Drop score
    for r in results:
        r.pop("_score")

    return {
        "query": q,
        "total": len(results),
        "tools": results[:limit],
    }


@router.get("/featured")
async def get_featured():
    """Curated featured tools - 5 from each major category."""
    cat = _get_full_catalog()
    return {
        "total": len(cat["featured"]),
        "tools": cat["featured"],
    }


@router.get("/by-trial")
async def get_by_trial():
    """Tools grouped by free trial tier (free/standard/premium)."""
    cat = _get_full_catalog()
    return {
        "tiers": cat["trial_tiers"],
        "groups": dict(cat["by_trial_tier"].items()),
        "counts": {k: len(v) for k, v in cat["by_trial_tier"].items()},
    }


@router.get("/by-price")
async def get_by_price():
    """Tools grouped by price tier (micro/low/mid/high/premium)."""
    cat = _get_full_catalog()
    return {
        "tiers": cat["price_tiers"],
        "groups": dict(cat["by_price_tier"].items()),
        "counts": {k: len(v) for k, v in cat["by_price_tier"].items()},
    }


@router.get("/by-chain/{chain}")
async def get_by_chain(chain: str):
    """All tools available for a specific chain."""
    cat = _get_full_catalog()
    chain_upper = chain.upper()
    tools = [t for t in cat["tools"] if chain_upper in [c.upper() for c in t["chains"]]]
    return {
        "chain": chain_upper,
        "total": len(tools),
        "tools": tools,
    }


@router.get("/by-category/{category}")
async def get_by_category(category: str):
    """All tools in a specific category."""
    cat = _get_full_catalog()
    tools = [t for t in cat["tools"] if t["category"].lower() == category.lower()]
    return {
        "category": category.lower(),
        "total": len(tools),
        "tools": tools,
    }


@router.get("/by-service/{service}")
async def get_by_service(service: str):
    """All tools from a specific service."""
    cat = _get_full_catalog()
    tools = [t for t in cat["tools"] if t["service"].lower() == service.lower()]
    return {
        "service": service.lower(),
        "total": len(tools),
        "tools": tools,
    }


@router.get("/stats")
async def get_stats():
    """Catalog statistics - counts, breakdown, pricing."""
    cat = _get_full_catalog()
    return {
        "version": cat["version"],
        "generated_at": cat["generated_at"],
        "total_tools": cat["total_tools"],
        "total_native": cat["total_native"],
        "total_external": cat["total_external"],
        "total_services": cat["total_services"],
        "total_categories": cat["total_categories"],
        "total_chains": cat["total_chains"],
        "by_category": {k: len(v) for k, v in cat["by_category"].items()},
        "by_service": {k: len(v) for k, v in cat["by_service"].items()},
        "by_trial_tier": {k: len(v) for k, v in cat["by_trial_tier"].items()},
        "by_price_tier": {k: len(v) for k, v in cat["by_price_tier"].items()},
        "external_services": cat["external_services"],
    }


@router.get("/external")
async def get_external():
    """All free open-source MCPs we've integrated, with their metadata."""
    cat = _get_full_catalog()
    return {
        "total": len(cat["external_services"]),
        "services": cat["external_services"],
        "tools": [t for t in cat["tools"] if t["source"] in ("expanded-mcp", "mcp-router")],
    }


@router.get("/{tool_id}")
async def get_tool(tool_id: str):
    """Single tool with full metadata."""
    cat = _get_full_catalog()
    for t in cat["tools"]:
        if t["id"] == tool_id:
            return t
    raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")


@router.post("/invalidate-cache")
async def invalidate():
    """Force catalog rebuild on next request. Call after adding tools."""
    invalidate_catalog_cache()
    return {"status": "ok", "message": "Cache invalidated - next request will rebuild"}


# ════════════════════════════════════════════════════════════
# Convenience: get_total_tool_count (used by other routers)
# ════════════════════════════════════════════════════════════


def get_total_tool_count() -> int:
    return _get_full_catalog().get("total_tools", 0)
