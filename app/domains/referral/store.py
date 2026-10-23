"""Persistence for referral attribution — Postgres canonical, ClickHouse mirror."""

from __future__ import annotations

import asyncio
import uuid  # noqa: TC003 — SQLAlchemy Mapped[] / annotations resolve at runtime
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 — annotation resolves at runtime

from app.core.logging import get_logger
from app.domains.referral.models import ReferralClick

log = get_logger(__name__)


class ReferralStore:
    """Thin repository over ``referral_clicks`` (one row per attribution event)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        partner: str,
        code: str,
        surface: str,
        tier: str,
        referrer_hash: str | None,
        chat_hash: str | None,
    ) -> uuid.UUID:
        row = ReferralClick(
            partner=partner,
            code=code,
            surface=surface,
            tier=tier,
            referrer_hash=referrer_hash,
            chat_hash=chat_hash,
        )
        self._session.add(row)
        await self._session.commit()
        await self._mirror(partner=partner, code=code, surface=surface, tier=tier)
        return row.click_id

    async def _mirror(self, **row: str) -> None:
        """Best-effort ClickHouse analytics copy — never fails the request."""
        try:
            from app.domains.referral.clickhouse_sink import mirror_click

            await asyncio.to_thread(mirror_click, row)
        except Exception as exc:  # mirror is non-critical
            log.warning("referral_clickhouse_mirror_failed: %s", exc)

    async def stats(self, *, partner: str | None = None, days: int = 7, limit: int = 100) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(
                ReferralClick.partner,
                ReferralClick.code,
                ReferralClick.surface,
                ReferralClick.tier,
                func.count().label("clicks"),
            )
            .where(ReferralClick.landed_at >= since)
            .group_by(
                ReferralClick.partner,
                ReferralClick.code,
                ReferralClick.surface,
                ReferralClick.tier,
            )
            .order_by(func.count().desc())
            .limit(limit)
        )
        if partner:
            stmt = stmt.where(ReferralClick.partner == partner)
        result = await self._session.execute(stmt)
        return [dict(row._mapping) for row in result]
