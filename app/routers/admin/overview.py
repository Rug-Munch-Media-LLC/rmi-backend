"""Platform-wide stats overview for the admin dashboard.

GET /api/v1/admin/overview (admin key required)

Single endpoint aggregating key platform metrics:
  - Scans: 24h scan volume, unique tokens, per-chain breakdown (Postgres
    ``scan_results`` — canonical, written by the scanner pipeline).
  - Alerts: active alerts + severity breakdown (Redis ``rmi:alerts`` hash).
  - Content: Qdrant ``news_v2`` article counts, feed posts, Ghost drafts.
  - Users: active API keys (Redis), bot users + premium tiers (bot SQLite).
  - System: uptime, version, healthy stores, DataBus provider status.

Every section fails open to ``None`` — a broken backend degrades one stat,
never the whole overview. All fetches run in parallel via ``asyncio.gather``.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import sqlite3
from typing import Any, cast

from fastapi import APIRouter, Depends

from app.core.logging import get_logger
from app.routers.admin import verify_admin

logger = get_logger(__name__)

overview_router = APIRouter(tags=["admin-overview"])

_PREMIUM_TIERS = ("scout", "hunter", "pro")
_SEVERITIES = ("critical", "high", "medium", "low")


# ══════════════════════════════════════════════
# Scans (Postgres scan_results)
# ══════════════════════════════════════════════


async def _scan_stats() -> dict[str, Any]:
    """24h scan count, unique tokens scanned, scans by chain."""
    try:
        from sqlalchemy import func, select

        from app.core.database import async_session_factory
        from app.models.scan import ScanResult

        since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=24)
        async with async_session_factory() as session:
            total = (
                await session.execute(
                    select(func.count()).select_from(ScanResult).where(ScanResult.scanned_at >= since)
                )
            ).scalar()
            unique = (
                await session.execute(
                    select(func.count(func.distinct(ScanResult.token_address))).where(ScanResult.scanned_at >= since)
                )
            ).scalar()
            by_chain_rows = (
                await session.execute(
                    select(ScanResult.chain, func.count())
                    .where(ScanResult.scanned_at >= since)
                    .group_by(ScanResult.chain)
                )
            ).all()
        return {
            "scans_24h": int(total or 0),
            "unique_tokens": int(unique or 0),
            "by_chain": {str(chain): int(c) for chain, c in by_chain_rows},
        }
    except Exception as exc:
        logger.debug("overview_scan_stats_failed err=%s", str(exc)[:200])
        return {"scans_24h": None, "unique_tokens": None, "by_chain": None}


# ══════════════════════════════════════════════
# Alerts (Redis rmi:alerts)
# ══════════════════════════════════════════════


async def _alert_stats() -> dict[str, Any]:
    """Active alerts and severity breakdown from the Redis alerts hash."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return {"active": None, "by_severity": None}
        raw = await r.hgetall("rmi:alerts")
        by_severity: dict[str, int] = dict.fromkeys(_SEVERITIES, 0)
        active = 0
        for alert_json in raw.values():
            try:
                alert = json.loads(alert_json)
            except (TypeError, ValueError) as exc:
                logger.debug("overview_alert_parse_failed err=%s", str(exc)[:100])
                continue
            if not isinstance(alert, dict):
                continue
            status = str(alert.get("status", "")).lower()
            if status in ("resolved", "dismissed", "closed"):
                continue
            active += 1
            sev = str(alert.get("severity", "")).lower()
            if sev in by_severity:
                by_severity[sev] += 1
        return {"active": active, "by_severity": by_severity}
    except Exception as exc:
        logger.debug("overview_alert_stats_failed err=%s", str(exc)[:200])
        return {"active": None, "by_severity": None}


# ══════════════════════════════════════════════
# Content (Qdrant news_v2, feed, Ghost drafts)
# ══════════════════════════════════════════════


async def _article_stats() -> dict[str, Any]:
    """Qdrant news_v2 totals — reuses the content-health Qdrant probe."""
    try:
        import httpx

        base = os.getenv("QDRANT_URL", os.getenv("QDRANT_FALLBACK", "http://rmi-qdrant-v2:6333")).rstrip("/")
        async with httpx.AsyncClient(timeout=10) as client:
            info = await client.get(f"{base}/collections/news_v2")
            total = info.json().get("result", {}).get("points_count") if info.status_code == 200 else None
            since = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=24)).isoformat()
            count = await client.post(
                f"{base}/collections/news_v2/points/count",
                json={"filter": {"must": [{"key": "indexed_at", "range": {"gte": since}}]}},
            )
            last24h = count.json().get("result", {}).get("count") if count.status_code == 200 else None
        return {"total_articles": total, "articles_24h": last24h}
    except Exception as exc:
        logger.debug("overview_article_stats_failed err=%s", str(exc)[:200])
        return {"total_articles": None, "articles_24h": None}


async def _feed_post_count() -> int | None:
    """Number of posts currently live on the feed (Ghost Content API)."""
    try:
        from app.content_platform import get_feed

        feed = await get_feed(page=1, limit=1)
        meta = feed.get("meta", {}) if isinstance(feed, dict) else {}
        pagination = meta.get("pagination", {}) if isinstance(meta, dict) else {}
        total = pagination.get("total")
        if total is None:
            posts = feed.get("posts", []) if isinstance(feed, dict) else []
            total = len(posts)
        return int(total)
    except Exception as exc:
        logger.debug("overview_feed_count_failed err=%s", str(exc)[:200])
        return None


async def _ghost_draft_count() -> int | None:
    """Number of Ghost drafts (Ghost Content API, status:draft)."""
    try:
        import httpx

        from app.content_platform import GHOST_API, GHOST_CONTENT_KEY

        if not GHOST_CONTENT_KEY:
            return None
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GHOST_API}/ghost/api/content/posts/",
                params={
                    "key": GHOST_CONTENT_KEY,
                    "limit": 1,
                    "filter": "status:draft",
                },
                headers={"Accept-Version": "v5.0"},
            )
            if resp.status_code != 200:
                return None
            meta = resp.json().get("meta", {}).get("pagination", {})
            total = meta.get("total")
            return int(total) if total is not None else None
    except Exception as exc:
        logger.debug("overview_ghost_drafts_failed err=%s", str(exc)[:200])
        return None


# ══════════════════════════════════════════════
# Users (Redis API keys + bot SQLite)
# ══════════════════════════════════════════════


async def _api_key_stats() -> dict[str, Any]:
    """Active API keys from the Redis key store."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return {"active": None, "total": None}
        keys = await r.hgetall("rmi:api_keys")
        active = 0
        for raw in keys.values():
            try:
                d = json.loads(raw)
            except (TypeError, ValueError) as exc:
                logger.debug("overview_api_key_parse_failed err=%s", str(exc)[:100])
                continue
            if isinstance(d, dict) and d.get("status", "active") == "active":
                active += 1
        return {"active": active, "total": len(keys)}
    except Exception as exc:
        logger.debug("overview_api_key_stats_failed err=%s", str(exc)[:200])
        return {"active": None, "total": None}


def _resolve_bot_db_path() -> str:
    candidates = [
        os.getenv("BOT_DB_PATH", ""),
        "/app/bot-src/rugmunchbot/data/bot_users.db",
        "/tmp/rmi/telegram/bot_users.db",  # noqa: S108  # nosec B108 — known bot DB mirror path
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    try:
        from rugmunchbot import db as bot_db

        return bot_db.DB_PATH
    except Exception:
        return "/app/bot-src/rugmunchbot/data/bot_users.db"


async def _bot_user_stats() -> dict[str, Any]:
    """Bot user count + premium tier breakdown from the bot SQLite DB."""
    try:
        conn = sqlite3.connect(_resolve_bot_db_path(), timeout=5)
        try:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            premium = 0
            by_tier: dict[str, int] = {}
            try:
                rows = conn.execute("SELECT tier, COUNT(*) FROM users GROUP BY tier").fetchall()
                for tier, count in rows:
                    tier = str(tier or "free").lower()
                    by_tier[tier] = int(count)
                    if tier in _PREMIUM_TIERS:
                        premium += int(count)
            except sqlite3.Error:
                pass  # tier column may not exist in older schemas
            return {"bot_users": int(total), "premium_users": premium, "by_tier": by_tier}
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        logger.debug("overview_bot_user_stats_failed err=%s", str(exc)[:200])
        return {"bot_users": None, "premium_users": None, "by_tier": None}


# ══════════════════════════════════════════════
# System (health endpoint + DataBus providers)
# ══════════════════════════════════════════════


async def _system_stats() -> dict[str, Any]:
    """Uptime, version, healthy store count from the /health probe."""
    try:
        from app.core.health_route import health

        resp = await health()
        stores = resp.stores if hasattr(resp, "stores") else {}
        healthy = sum(
            1 for s in stores.values() if getattr(s, "healthy", False) or (isinstance(s, dict) and s.get("healthy"))
        )
        return {
            "uptime_seconds": getattr(resp, "uptime_seconds", None),
            "version": getattr(resp, "version", None),
            "status": getattr(resp, "status", None),
            "healthy_stores": healthy,
            "total_stores": len(stores),
        }
    except Exception as exc:
        logger.debug("overview_system_stats_failed err=%s", str(exc)[:200])
        return {
            "uptime_seconds": None,
            "version": None,
            "status": None,
            "healthy_stores": None,
            "total_stores": None,
        }


async def _databus_stats() -> dict[str, Any]:
    """DataBus provider health summary."""
    try:
        from app.routers.v1.providers import providers_status

        status = await providers_status()
        providers = status.get("providers", []) if isinstance(status, dict) else []
        return {
            "total": status.get("total") if isinstance(status, dict) else len(providers),
            "healthy": status.get("healthy") if isinstance(status, dict) else None,
            "unhealthy": status.get("unhealthy") if isinstance(status, dict) else None,
        }
    except Exception as exc:
        logger.debug("overview_databus_stats_failed err=%s", str(exc)[:200])
        return {"total": None, "healthy": None, "unhealthy": None}


# ══════════════════════════════════════════════
# Endpoint
# ══════════════════════════════════════════════


@overview_router.get("/overview", summary="Platform Stats Overview")
async def platform_overview(_=Depends(verify_admin)) -> dict[str, Any]:
    """Platform-wide stats overview for admin dashboard."""
    results = await asyncio.gather(
        _scan_stats(),
        _alert_stats(),
        _article_stats(),
        _feed_post_count(),
        _ghost_draft_count(),
        _api_key_stats(),
        _bot_user_stats(),
        _system_stats(),
        _databus_stats(),
    )
    scans, alerts, articles, feed_posts, ghost_drafts, api_keys, bot_users, system, databus = results  # type: ignore[misc]

    scans_d, alerts_d, articles_d = (
        cast("dict[str, Any]", scans),
        cast("dict[str, Any]", alerts),
        cast("dict[str, Any]", articles),
    )
    api_keys_d, bot_users_d, system_d, databus_d = (
        cast("dict[str, Any]", api_keys),
        cast("dict[str, Any]", bot_users),
        cast("dict[str, Any]", system),
        cast("dict[str, Any]", databus),
    )

    return {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "scans": scans_d,
        "alerts": alerts_d,
        "content": {
            "total_articles": articles_d.get("total_articles"),
            "articles_24h": articles_d.get("articles_24h"),
            "feed_posts": feed_posts,
            "ghost_drafts": ghost_drafts,
        },
        "users": {
            "api_keys_active": api_keys_d.get("active"),
            "api_keys_total": api_keys_d.get("total"),
            "bot_users": bot_users_d.get("bot_users"),
            "premium_users": bot_users_d.get("premium_users"),
            "tiers": bot_users_d.get("by_tier"),
        },
        "system": {**system_d, "databus": databus_d},
    }
