"""FastAPI dependencies for referral attribution."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 — FastAPI resolves it at runtime

from app.core.database import get_db
from app.core.internal_key import get_internal_key_or_none
from app.domains.referral.store import ReferralStore


async def get_store(session: AsyncSession = Depends(get_db)) -> ReferralStore:
    """Session-scoped store; override in tests with an in-memory fake."""
    return ReferralStore(session)


async def require_internal_key(x_rmi_key: str | None = Header(default=None)) -> None:
    """Guard read-side analytics — partner/code funnels are not public data."""
    expected = get_internal_key_or_none()
    if not expected or not x_rmi_key or not hmac.compare_digest(x_rmi_key, expected):
        raise HTTPException(status_code=401, detail="invalid internal key")
