"""Alert ingestion — turn a real scan result into persisted alerts.

POST /api/v1/alerts/scan-result

The scanner's detectors already decide what happened; this endpoint exists so
that decision reaches the ``alerts`` table instead of dying in a log line. It
persists only detectors that fired, and the row id is deterministic, so
posting the same scan twice yields one row.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.core.logging import get_logger
from app.domains.alerts.postgres_store import save_alerts
from app.domains.alerts.scan_alerts import alerts_from_scan

log = get_logger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts-ingest"])


@router.post("/scan-result")
async def ingest_scan_result(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Persist one alert per detector that fired in a scanner result.

    Returns the derived rows and how many were newly written, so a caller can
    tell a genuine first sighting from a duplicate delivery.
    """
    rows = alerts_from_scan(payload)
    inserted = await save_alerts(session, rows) if rows else 0
    log.info(
        "scan_alerts_ingested token=%s chain=%s derived=%d inserted=%d",
        str(payload.get("token"))[:16],
        payload.get("chain"),
        len(rows),
        inserted,
    )
    return {
        "token": payload.get("token"),
        "chain": payload.get("chain"),
        "derived": len(rows),
        "inserted": inserted,
        "alerts": [
            {
                "alert_id": r["alert_id"],
                "alert_type": r["alert_type"],
                "severity": r["severity"],
                "title": r["title"],
            }
            for r in rows
        ],
    }
