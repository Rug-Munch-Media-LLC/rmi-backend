"""
RMI Community Forensics API Router
===================================
Thin router wrapper for community forensics — reports, leaderboards,
graph enrichment, and cross-chain correlation.

Endpoints:
  GET  /api/v1/community-forensics/reports        — List community reports
  GET  /api/v1/community-forensics/reports/{id}    — Get report detail
  POST /api/v1/community-forensics/reports         — Submit new report
  GET  /api/v1/community-forensics/leaderboard     — Top community sleuths
  POST /api/v1/community-forensics/correlate       — Cross-chain address correlation
  POST /api/v1/community-forensics/verify-graph    — Verify & enrich report graph
  GET  /api/v1/community-forensics/health           — Service health check
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.workers.community_forensics_worker import CommunityForensicsWorker

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/community-forensics", tags=["community-forensics"])

# Lazy worker init
_worker: CommunityForensicsWorker | None = None


def _get_worker() -> CommunityForensicsWorker:
    global _worker
    if _worker is None:
        _worker = CommunityForensicsWorker()
    return _worker


# ── Redis helpers ─────────────────────────────────────
async def _get_redis():
    import redis.asyncio as aioredis

    return aioredis.Redis(
        host=os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", "") or None,
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
    )


# ── Models ────────────────────────────────────────────


class ReportSubmit(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10, max_length=5000)
    category: str = Field(default="scam", pattern="^(scam|exploit|phishing|rugpull|other)$")
    addresses: list[str] = Field(default_factory=list, max_length=20)
    graph_data: dict = Field(default_factory=dict)
    author: str = Field(default="anonymous", max_length=100)


class CorrelationRequest(BaseModel):
    addresses: list[str] = Field(..., min_length=1, max_length=20)


class GraphVerifyRequest(BaseModel):
    report_id: str = Field(..., min_length=1)
    graph_data: dict = Field(default_factory=dict)


# ── Routes ────────────────────────────────────────────


@router.get("/reports", summary="List Community Reports")
async def list_reports(
    category: str = "",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List community-submitted forensic reports (newest first)."""
    try:
        r = await _get_redis()
        report_ids = await r.zrevrange("community:forensics:reports:index", offset, offset + limit - 1)

        reports = []
        for rid in report_ids:
            data = await r.get(f"community:forensics:report:{rid}")
            if data:
                report = json.loads(data)
                if not category or report.get("category") == category:
                    # Strip sensitive fields for list view
                    reports.append(
                        {
                            "id": report.get("id"),
                            "title": report.get("title"),
                            "category": report.get("category"),
                            "author": report.get("author"),
                            "addresses_count": len(report.get("addresses", [])),
                            "created_at": report.get("created_at"),
                            "updated_at": report.get("updated_at"),
                        }
                    )

        count = await r.zcard("community:forensics:reports:index")
        return {"reports": reports, "total": count, "limit": limit, "offset": offset}
    except Exception as e:
        logger.warning(f"community_forensics_reports_list_failed: {e}")
        return {"reports": [], "total": 0, "limit": limit, "offset": offset}


@router.get("/reports/{report_id}", summary="Get Report Detail")
async def get_report(report_id: str) -> dict:
    """Get full details of a community forensics report."""
    try:
        r = await _get_redis()
        data = await r.get(f"community:forensics:report:{report_id}")
        if not data:
            raise NotFoundError(message=f"Report {report_id} not found")
        return json.loads(data)
    except NotFoundError:
        raise
    except Exception as e:
        logger.warning(f"community_forensics_report_get_failed: {e}")
        raise NotFoundError(message=f"Report {report_id} not found")


@router.post("/reports", summary="Submit Community Report")
async def submit_report(req: ReportSubmit) -> dict:
    """Submit a new community forensics report."""
    report_id = f"cfr-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()

    report = {
        "id": report_id,
        "title": req.title,
        "description": req.description,
        "category": req.category,
        "addresses": req.addresses,
        "graph_data": req.graph_data,
        "author": req.author,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }

    try:
        r = await _get_redis()
        await r.set(f"community:forensics:report:{report_id}", json.dumps(report))
        await r.zadd("community:forensics:reports:index", {report_id: datetime.now(UTC).timestamp()})
        logger.info(f"community_forensics_report_submitted report_id={report_id}")
        return {"status": "ok", "report_id": report_id, "report": report}
    except Exception as e:
        logger.error(f"community_forensics_report_submit_failed: {e}")
        raise HTTPException(status_code=503, detail="Storage temporarily unavailable")


@router.post("/verify-graph", summary="Verify Report Graph")
async def verify_graph(req: GraphVerifyRequest) -> dict:
    """Verify and enrich a report's graph data with Blockscout lookups."""
    worker = _get_worker()
    try:
        result = await worker.verify_report_graph(req.report_id, req.graph_data)
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"graph_verification_failed: {e}")
        raise HTTPException(status_code=500, detail=f"Graph verification failed: {e}")


@router.post("/correlate", summary="Cross-Chain Correlation")
async def correlate_addresses(req: CorrelationRequest) -> dict:
    """Batch cross-chain correlation for community investigation addresses."""
    worker = _get_worker()
    try:
        results = await worker.batch_cross_chain_correlate(req.addresses)
        return {"status": "ok", "results": results, "total": len(results)}
    except Exception as e:
        logger.error(f"cross_chain_correlation_failed: {e}")
        raise HTTPException(status_code=500, detail=f"Correlation failed: {e}")


@router.get("/leaderboard", summary="Community Leaderboard")
async def get_leaderboard(period: str = Query(default="weekly", pattern="^(daily|weekly|monthly|all)$")) -> dict:
    """Get community forensics leaderboard (top sleuths by contributions)."""
    try:
        r = await _get_redis()
        key = f"community:forensics:leaderboard:{period}"
        entries = await r.zrevrange(key, 0, 19, withscores=True)

        leaderboard = []
        for username, score in entries:
            # Get user stats
            stats_raw = await r.get(f"community:forensics:user:{username}")
            stats = json.loads(stats_raw) if stats_raw else {}
            leaderboard.append(
                {
                    "username": username,
                    "score": int(score),
                    "reports": stats.get("reports", 0),
                    "verifications": stats.get("verifications", 0),
                }
            )

        return {"leaderboard": leaderboard, "period": period, "count": len(leaderboard)}
    except Exception as e:
        logger.warning(f"community_forensics_leaderboard_failed: {e}")
        return {"leaderboard": [], "period": period, "count": 0}


@router.get("/health", summary="Forensics Service Health")
async def health_check() -> dict:
    """Check if Blockscout and Redis are available."""
    worker = _get_worker()
    health = {"redis": "unknown", "blockscout": "unknown"}
    try:
        r = await _get_redis()
        await r.ping()
        health["redis"] = "ok"
    except Exception:
        health["redis"] = "down"

    try:
        resp = await worker.blockscout.get("/api/v2/main-page/indexing-status")
        health["blockscout"] = "ok" if resp.status_code == 200 else "degraded"
    except Exception as e:
        health["blockscout"] = f"down: {e}"

    return {"status": "ok" if all(v == "ok" for v in health.values()) else "degraded", "services": health}
