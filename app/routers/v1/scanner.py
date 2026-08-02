"""V1 scanner endpoints — /api/v1/scanner/*.

Serves ``GET /api/v1/scanner/recent`` consumed by the internal news
service (``app/domains/news/news_service/fetchers_api.py``) to build
"RMI Scanner" security news items from recent scan results.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.logging import get_logger
from app.routers.v1.data import fetch_recent_scans

logger = get_logger(__name__)

router = APIRouter(prefix="/scanner", tags=["scanner"])


@router.get("/recent")
async def recent_scans(
    limit: int = Query(default=10, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Recent scan results (newest first) as a plain JSON array.

    Consumed by the news fetcher: each item exposes ``token_name``,
    ``contract_address`` / ``token_address``, ``chain``, ``risk_score``,
    ``is_honeypot``, ``is_rug_pull``, ``liquidity_locked``,
    ``has_mint_authority`` and ``scanned_at``.
    """
    return await fetch_recent_scans(limit=limit)
