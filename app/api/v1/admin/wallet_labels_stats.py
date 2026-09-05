"""Admin wallet-label RAG stats - /api/v1/admin/wallet-labels/stats.

Read-only counters for the wallet label RAG bridge (``app/wallet_memory/
rag_labels.py``): Qdrant collection size, labels added in the last 24h,
and auto-suggested labels pending review. Auth is enforced by the global
auth middleware (same pattern as the alerts webhook admin router).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/wallet-labels", tags=["admin"])


@router.get("/stats")
async def wallet_label_stats() -> dict[str, Any]:
    """Wallet label RAG stats (Qdrant totals, 24h additions, pending suggestions)."""
    from app.wallet_memory.rag_labels import label_stats

    try:
        return await label_stats()
    except Exception as exc:  # pragma: no cover - defensive, fail-open
        log.warning("wallet_label_stats_failed err=%s", exc)
        return {
            "collection": "wallet_labels",
            "total_labels_in_qdrant": 0,
            "labels_added_last_24h": 0,
            "auto_suggested_pending_review": 0,
            "error": "stats_unavailable",
        }
