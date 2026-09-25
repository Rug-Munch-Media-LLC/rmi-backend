"""Shill-campaign intelligence API Router (read/write, fail-open).

Exposes the moved shill-campaign intelligence so BOTH the bot and the
frontend consume it via API instead of the bot holding its own copy:

  GET   /api/v1/shill/health          — service health
  GET   /api/v1/shill/campaigns       — list shill campaigns (evidence-backed)
  POST  /api/v1/shill/campaigns/detect — detect campaigns from posted articles
  GET   /api/v1/shill/probe/{handle}  — probe an X handle (GetXAPI profile)
  GET   /api/v1/shill/evidence        — get shill evidence (token/chain/account)
  GET   /api/v1/shill/evidence/search — semantic search over shill evidence
  POST  /api/v1/shill/evidence        — persist one shill evidence record
  GET   /api/v1/shill/feed            — Wall-of-Shame / shill feed (reused)

Wall-of-Shame + scorecard + leaderboard deliberately reuse the existing
``/api/v1/kol/*`` routes (``app.routers.kol_intel``) — this router adds only
the shill-specific surface and the /feed alias onto the same store. Every
handler degrades to a structured empty ``200`` on upstream failure — never 500.

These GET endpoints are public (no JWT) so the bot + frontend can read them
without a session; the token-scoped reads already live under /api/v1/kol/*.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency
from app.domains.shill import service

logger = get_logger("shill_router")

router = APIRouter(prefix="/api/v1/shill", tags=["social-intel"])


# ── Request/response models ────────────────────────────────────────────────


class ArticleIn(BaseModel):
    """A normalized article the caller wants campaign-detected."""

    title: str = ""
    text: str = ""
    source_channel: str = ""
    channel: str = ""
    url: str = ""
    source_url: str = ""
    entities: list[str] = []
    posted_at: str = ""


class DetectRequest(BaseModel):
    articles: list[ArticleIn] = []
    window_hours: int | None = None
    min_accounts: int | None = None
    min_mentions: int | None = None


class EvidenceIn(BaseModel):
    token: str = ""
    chain: str = ""
    account: str = ""
    url: str = ""
    x_url: str = ""
    image_path: str = ""
    text: str = ""
    ocr_text: str = ""
    #: Capture origin ("x" | "telegram") so the site can distinguish where a
    #: screenshot came from. Empty → legacy default (account-derived).
    source: str = ""


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    summary="Shill Service Health",
    dependencies=[Depends(get_rate_limit_dependency("shill_h", rate=2.0, burst=5))],
)
def health() -> dict[str, Any]:
    """GetXAPI key state + evidence availability. Never raises."""
    try:
        from app.domains.shill import getxapi

        evidence = service.get_evidence(limit=1)
        return {
            "status": "ok",
            "x_provider": "getxapi",
            "x_provider_key_configured": getxapi.key_configured(),
            "evidence_count": evidence.get("total", 0),
            "source": "shill_service",
        }
    except Exception as exc:  # graceful degradation contract
        logger.warning("shill_health_failed: %s", str(exc)[:120])
        return {"status": "degraded", "source": "shill_service"}


@router.get(
    "/campaigns",
    summary="List Shill Campaigns (evidence-backed)",
    dependencies=[Depends(get_rate_limit_dependency("shill_c1", rate=2.0, burst=5))],
)
def list_campaigns(
    token: str = Query(default="", description="Filter by token (address or $TICKER)"),
    chain: str = Query(default="", description="Filter by chain"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Aggregate shill evidence rows into a campaign list. Never raises."""
    try:
        return service.list_campaigns(token=token, chain=chain, limit=limit)
    except Exception as exc:  # graceful degradation contract
        logger.warning("shill_campaigns_failed: %s", str(exc)[:120])
        return {"campaigns": [], "total": 0, "source": "shill_evidence"}


@router.post(
    "/campaigns/detect",
    summary="Detect Shill Campaigns from Articles",
    dependencies=[Depends(get_rate_limit_dependency("shill_c2", rate=2.0, burst=5))],
)
async def detect_campaigns(req: DetectRequest) -> dict[str, Any]:
    """Run coordinated-shill detection on a batch of article dicts.

    Public-ish POST (no JWT) so the bot can push its sweep results here and
    read the campaign verdict back. Fail-open → ``{"campaigns": [], "total": 0}``.
    """
    try:
        articles = [a.model_dump() for a in req.articles]
        campaigns = await service.detect_campaigns_from_articles(
            articles,
            window_hours=req.window_hours,
            min_accounts=req.min_accounts,
            min_mentions=req.min_mentions,
        )
        return {"campaigns": [c.to_dict() for c in campaigns], "total": len(campaigns), "source": "shill_detector"}
    except Exception as exc:  # graceful degradation contract
        logger.warning("shill_detect_failed: %s", str(exc)[:120])
        return {"campaigns": [], "total": 0, "source": "shill_detector"}


@router.get(
    "/probe/{handle}",
    summary="Probe an X Handle (GetXAPI profile)",
    dependencies=[Depends(get_rate_limit_dependency("shill_p1", rate=2.0, burst=5))],
)
async def probe_handle(handle: str, force: bool = Query(default=False)) -> dict[str, Any] | None:
    """GetXAPI X profile for one handle (cached, fail-open).

    Returns ``None`` (200) when the key is unconfigured / rate-limited / the
    API errors — the caller degrades. Never a 500.
    """
    key = handle.strip().lstrip("@")
    if not key:
        return None
    try:
        profile = await service.probe_handle(key, force=force)
        return profile
    except Exception as exc:  # graceful degradation contract
        logger.warning("shill_probe_failed: %s", str(exc)[:120])
        return None


@router.get(
    "/evidence",
    summary="Get Shill Evidence",
    dependencies=[Depends(get_rate_limit_dependency("shill_e1", rate=2.0, burst=5))],
)
def get_evidence(
    token: str = Query(default=""),
    chain: str = Query(default=""),
    account: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    """Return shill evidence rows (screenshot refs + OCR), filtered. Never raises."""
    try:
        return service.get_evidence(token=token, chain=chain, account=account, limit=limit)
    except Exception as exc:  # graceful degradation contract
        logger.warning("shill_evidence_failed: %s", str(exc)[:120])
        return {"evidence": [], "total": 0, "source": "shill_evidence"}


@router.get(
    "/evidence/search",
    summary="Semantic Search over Shill Evidence",
    dependencies=[Depends(get_rate_limit_dependency("shill_e2", rate=2.0, burst=5))],
)
async def search_evidence(
    q: str = Query(default="", description="Natural-language query"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """Semantic search over the shill evidence Qdrant collection. Fail-open."""
    try:
        query = q.strip()
        if not query:
            return {"hits": [], "total": 0, "source": "qdrant_shill_evidence"}
        return await service.search_shill_evidence(query, limit=limit)
    except Exception as exc:  # graceful degradation contract
        logger.warning("shill_evidence_search_failed: %s", str(exc)[:120])
        return {"hits": [], "total": 0, "source": "qdrant_shill_evidence"}


@router.post(
    "/evidence",
    summary="Persist Shill Evidence",
    dependencies=[Depends(get_rate_limit_dependency("shill_e3", rate=2.0, burst=5))],
)
async def add_evidence(req: EvidenceIn) -> dict[str, Any]:
    """Persist one shill evidence record (Qdrant mirror + bot DB, fail-open)."""
    try:
        record = req.model_dump()
        return await service.add_evidence(record)
    except Exception as exc:  # graceful degradation contract
        logger.warning("shill_evidence_add_failed: %s", str(exc)[:120])
        return {"saved": False, "source": "shill_evidence"}


@router.get(
    "/feed",
    summary="Wall-of-Shame / Shill Feed",
    dependencies=[Depends(get_rate_limit_dependency("shill_f1", rate=2.0, burst=5))],
)
def feed(
    severity: str = Query(default="", description="Filter by severity (critical|high|medium|low)"),
) -> dict[str, Any]:
    """Wall-of-Shame feed — reused from the existing ``/api/v1/kol`` store.

    Deliberately NOT a duplicate: the canonical data source is
    ``app.databus.kol_bot_store`` (the same one ``/api/v1/kol/wall-of-shame``
    reads). This route is the shill-domain alias the bot + frontend consume.
    Never raises.
    """
    try:
        return service.wall_of_shame_feed(severity)
    except Exception as exc:  # graceful degradation contract
        logger.warning("shill_feed_failed: %s", str(exc)[:120])
        return {"wall_of_shame": [], "total": 0, "severity": severity.strip() or None, "source": "wall_of_shame_json"}
