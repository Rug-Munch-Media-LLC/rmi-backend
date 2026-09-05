"""Admin deployer-repo stats - /api/v1/admin/deployer-repo/stats.

Read-only counters for the deployer reputation repository
(``app/ai/deployer_repo.py``): total deployers tracked, confirmed rugs,
repeat offenders. Auth is enforced by the global auth middleware (same
pattern as the wallet-labels stats admin router).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/deployer-repo", tags=["admin"])


@router.get("/stats")
async def deployer_repo_stats() -> dict[str, Any]:
    """Deployer repo stats (deployers tracked, confirmed rugs, repeat offenders)."""
    from app.ai.deployer_repo import get_deployer_repo

    try:
        return await get_deployer_repo().repo_stats()
    except Exception as exc:  # pragma: no cover - defensive, fail-open
        log.warning("deployer_repo_stats_endpoint_failed err=%s", exc)
        return {
            "total_deployers": 0,
            "total_launches_tracked": 0,
            "confirmed_rugs": 0,
            "confirmed_clean": 0,
            "pending_outcomes": 0,
            "repeat_offenders": 0,
            "available": False,
            "error": "stats_unavailable",
        }
