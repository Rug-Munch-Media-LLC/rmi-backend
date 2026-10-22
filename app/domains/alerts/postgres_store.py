"""Postgres persistence for platform alerts.

The canonical alert store is the ``alerts`` table created by
:mod:`app.catalog.init_schema`. Until now nothing wrote to it: the platform
alert pipeline (:func:`app.domains.alerts.service.ingest_and_route`) is pure
and returns a routing plan, and :mod:`app.alert_pipeline` only pushes to
Redis. The table therefore held zero rows forever.

This module is the single writer. Dedupe is structural, not advisory: the
row's ``alert_id`` is a deterministic hash of the event identity, so the
primary key makes a repeat insert a no-op via ``ON CONFLICT DO NOTHING``.
Callers pass already-derived rows; this module never invents content.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_INSERT = text(
    """
    INSERT INTO alerts (
        alert_id, token_id, wallet_id, chain, alert_type,
        severity, title, description, evidence
    ) VALUES (
        :alert_id, :token_id, :wallet_id, :chain, :alert_type,
        :severity, :title, :description, CAST(:evidence AS jsonb)
    )
    ON CONFLICT (alert_id) DO NOTHING
    """
)


def alert_id_for(*parts: str) -> str:
    """Deterministic 16-hex identity for an event.

    ``ON CONFLICT (alert_id) DO NOTHING`` turns any repeat of the same event
    into a no-op, so a re-scan or a redelivered message cannot duplicate.
    """
    raw = ":".join(str(p).lower() for p in parts)
    return "alert_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


async def save_alerts(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Insert alert rows, returning how many were genuinely new."""
    inserted = 0
    for row in rows:
        payload = dict(row)
        payload.setdefault("evidence", {})
        payload["evidence"] = json.dumps(payload["evidence"], default=str)
        result = await session.execute(_INSERT, payload)
        inserted += result.rowcount or 0
    await session.commit()
    return inserted
