"""
RMI Tool Changelog & Version System
=====================================
Tracks tool versions, changes, and deprecations.
Provides transparency for tool quality and evolution.

Endpoints:
  GET  /api/v1/tools/changelog       — Full changelog
  GET  /api/v1/tools/changelog/{tool} — Changelog for specific tool
  GET  /api/v1/tools/version/{tool}   — Current version for tool
  GET  /api/v1/tools/deprecated       — List of deprecated tools

Author: RMI Development
Date: 2026-06-05
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.redis import get_redis

logger = logging.getLogger("tool_changelog")

router = APIRouter(prefix="/api/v1/tools", tags=["tool-changelog"])


# ── Redis Helper ─────────────────────────────────────────────────


INITIAL_CHANGELOG = [
    {
        "date": "2026-06-05",
        "version": "3.3.0",
        "type": "feature",
        "tool": "whale_copy_trade",
        "title": "New Tool: Whale Copy Trade Engine",
        "description": "Real-time copy trade engine — input a smart money wallet, get their exact last 24h trades with entry/exit prices, PnL, and suggested follow trades.",
        "breaking": False,
    },
    {
        "date": "2026-06-05",
        "version": "3.3.0",
        "type": "feature",
        "tool": "rug_predictor_live",
        "title": "New Tool: Live Rug Predictor",
        "description": "Analyzes tokens in their first 5-30 minutes with 6-signal rug probability scoring. Detects low liquidity, unlocked LP, price crashes, mass selling, and more.",
        "breaking": False,
    },
    {
        "date": "2026-06-05",
        "version": "3.3.0",
        "type": "feature",
        "tool": "whale_cluster",
        "title": "New Tool: Whale Cluster Detection",
        "description": "Identify coordinated whale clusters — wallets that move together via same funding source, timing patterns, and token sets. Detects wash trading and insider networks.",
        "breaking": False,
    },
    {
        "date": "2026-06-05",
        "version": "3.3.0",
        "type": "infrastructure",
        "tool": "*",
        "title": "Developer Tier System",
        "description": "Free developer API keys with 100 calls/day, no payment required. Tier system: FREE (100/day), TRIAL (3-5/tool), PAID (pay-per-use), PRO ($29/mo, 5000/day).",
        "breaking": False,
    },
    {
        "date": "2026-06-05",
        "version": "3.3.0",
        "type": "infrastructure",
        "tool": "*",
        "title": "Facilitator Health Monitoring",
        "description": "60s health check loop for all payment facilitators with latency tracking, success rate monitoring, and auto-failover readiness.",
        "breaking": False,
    },
    {
        "date": "2026-06-05",
        "version": "3.3.0",
        "type": "infrastructure",
        "tool": "*",
        "title": "Public Status Page",
        "description": "Public-facing status page at /api/v1/status showing health of all RMI services — backend, Redis, ClickHouse, MCP, facilitators. 30s monitoring loop.",
        "breaking": False,
    },
    {
        "date": "2026-06-05",
        "version": "3.3.0",
        "type": "infrastructure",
        "tool": "*",
        "title": "Webhook Notification Pipeline",
        "description": "Real-time webhook delivery system with retry, signing, rate limiting, and dead letter queue. Polls every 5 seconds for new alerts.",
        "breaking": False,
    },
    {
        "date": "2026-06-05",
        "version": "3.3.0",
        "type": "infrastructure",
        "tool": "*",
        "title": "Persistent State Layer",
        "description": "User watchlists, portfolios, saved scans, and alert history. Redis-backed with JSON serialization.",
        "breaking": False,
    },
    {
        "date": "2026-06-05",
        "version": "3.3.0",
        "type": "improvement",
        "tool": "*",
        "title": "SDK v2.0 Released",
        "description": "Complete rewrite with async/sync dual API, retry with exponential backoff, batch tool calls, webhook management, proper error classes, and type hints.",
        "breaking": False,
    },
    {
        "date": "2026-06-01",
        "version": "3.2.0",
        "type": "improvement",
        "tool": "*",
        "title": "Response Enrichment Pipeline",
        "description": "Every tool response now enriched with wallet labels, RAG similarity search, scam pattern detection, risk summary, intel briefing, and confidence scores.",
        "breaking": False,
    },
    {
        "date": "2026-06-01",
        "version": "3.2.0",
        "type": "feature",
        "tool": "composite_score",
        "title": "New Tool: Composite Score",
        "description": "One-number buy/sell/avoid score combining reputation, rug probability, market health, narrative sentiment, MEV exposure, and DeFi position.",
        "breaking": False,
    },
    {
        "date": "2026-06-01",
        "version": "3.2.0",
        "type": "feature",
        "tool": "smart_money",
        "title": "New Tool: Smart Money Finder",
        "description": "Find profitable traders via win rate estimation, token count, balance analysis, and wallet label cross-reference.",
        "breaking": False,
    },
    {
        "date": "2026-05-28",
        "version": "3.1.0",
        "type": "infrastructure",
        "tool": "*",
        "title": "Multi-Facilitator Payment System",
        "description": "Smart router auto-picks best facilitator per chain/token. 7 active facilitators across 11 payment chains.",
        "breaking": False,
    },
]


# ── Changelog Management ─────────────────────────────────────────


def get_changelog(tool: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Get changelog entries."""
    r = get_redis()

    # Load from Redis if available
    entries = []
    if r:
        stored = r.lrange("rmi:changelog", 0, -1)
        entries = [json.loads(e) for e in stored]

    # If no stored entries, use initial data
    if not entries:
        entries = INITIAL_CHANGELOG.copy()
        if r:
            # Store initial entries
            for entry in reversed(entries):
                r.lpush("rmi:changelog", json.dumps(entry))

    # Filter by tool
    if tool:
        entries = [e for e in entries if e.get("tool") == tool or e.get("tool") == "*"]

    # Limit
    return entries[:limit]


def add_changelog_entry(
    tool: str,
    title: str,
    description: str,
    version: str = "",
    change_type: str = "improvement",
    breaking: bool = False,
):
    """Add a changelog entry."""
    r = get_redis()
    if not r:
        return

    entry = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "version": version or "current",
        "type": change_type,
        "tool": tool,
        "title": title,
        "description": description,
        "breaking": breaking,
    }

    r.lpush("rmi:changelog", json.dumps(entry))
    r.ltrim("rmi:changelog", 0, 499)  # Keep last 500


def get_tool_version(tool: str) -> dict[str, Any]:
    """Get current version info for a tool."""
    changelog = get_changelog(tool, limit=1)
    if changelog:
        entry = changelog[0]
        return {
            "tool": tool,
            "version": entry.get("version", "unknown"),
            "last_updated": entry.get("date", ""),
            "last_change": entry.get("title", ""),
        }

    # Default version
    return {
        "tool": tool,
        "version": "1.0.0",
        "last_updated": "unknown",
        "last_change": "No changelog entries",
    }


def get_deprecated_tools() -> list[dict[str, Any]]:
    """Get list of deprecated tools."""
    r = get_redis()
    if not r:
        return []

    deprecated = r.smembers("rmi:tools:deprecated")
    return [json.loads(d) for d in deprecated]


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/changelog")
async def tool_changelog(tool: str | None = None, limit: int = 50):
    """Get changelog entries."""
    entries = get_changelog(tool, limit)
    return JSONResponse(
        content={
            "changelog": entries,
            "total": len(entries),
            "filter": tool,
        }
    )


@router.get("/changelog/{tool}")
async def tool_specific_changelog(tool: str, limit: int = 20):
    """Get changelog for a specific tool."""
    entries = get_changelog(tool, limit)
    return JSONResponse(
        content={
            "tool": tool,
            "changelog": entries,
            "total": len(entries),
        }
    )


@router.get("/version/{tool}")
async def tool_version(tool: str):
    """Get current version for a tool."""
    return JSONResponse(content=get_tool_version(tool))


@router.get("/deprecated")
async def deprecated_tools():
    """Get list of deprecated tools."""
    return JSONResponse(
        content={
            "deprecated": get_deprecated_tools(),
            "count": len(get_deprecated_tools()),
        }
    )
