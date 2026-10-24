"""The ``boosted`` placement surface — our only paid-placement read path.

Rules encoded here (and asserted in the tests):

* Only rows in ``active`` status whose window has not closed are returned.
* Scope is always exactly one chain — a paid placement never crosses chains.
* External promotion feeds (DexScreener ``token-boosts``, GMGN) are NOT read
  here. Paid placement is our inventory only.
* Trending rank is a separate, unpaid clickhouse-derived surface
  (``app/routers/v1/market.py``); nothing in this module writes to it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.boost import BOOST_ACTIVE, BoostPurchase

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


async def list_active_boosts(
    session: AsyncSession,
    chain: str,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[BoostPurchase]:
    """Active, unexpired placements on ``chain`` (newest expiry first)."""
    moment = now or datetime.now(UTC)
    bounded = max(1, min(int(limit), MAX_LIMIT))
    stmt = (
        select(BoostPurchase)
        .where(
            BoostPurchase.chain == chain,
            BoostPurchase.status == BOOST_ACTIVE,
            BoostPurchase.ends_at.is_not(None),
            BoostPurchase.ends_at > moment,
        )
        .order_by(BoostPurchase.ends_at.desc())
        .limit(bounded)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
