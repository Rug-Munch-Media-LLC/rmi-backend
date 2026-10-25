"""Dependencies for the engagement domain — identity, ban state, storage.

Every seam is a FastAPI dependency, so tests substitute implementations through
``app.dependency_overrides`` (a DI seam) instead of monkeypatching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from fastapi import Depends, HTTPException, Request

from app.auth.jwt_validation import get_current_user
from app.core.database import get_db
from app.domains.engagement.permissions import Actor
from app.domains.engagement.sql_store import SqlEngagementStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domains.engagement.port import EngagementStore

_CLAIM_KEYS: tuple[str, ...] = ("id", "sub", "wallet")


class BanPolicy(Protocol):
    """Ban state seam."""

    async def is_banned(self, user_id: str) -> bool: ...


class BotStateBanPolicy:
    """Ban state from the shared bot admin store — the same source the bot reads.

    A store outage surfaces as an error (fail closed on writing) rather than
    silently allowing a banned account to post.
    """

    async def is_banned(self, user_id: str) -> bool:
        try:
            from rugmunchbot.db import banned_user_ids
        except Exception as exc:  # pragma: no cover — bot package absent in some deploys
            raise HTTPException(status_code=503, detail="ban state unavailable") from exc
        banned = await banned_user_ids()
        return str(user_id) in {str(item) for item in banned}


def get_ban_policy() -> BanPolicy:
    return BotStateBanPolicy()


def _claim_user_id(claims: dict[str, Any]) -> str:
    for key in _CLAIM_KEYS:
        value = str(claims.get(key) or "").strip()
        if value:
            return value
    return ""


async def get_optional_actor(request: Request) -> Actor | None:
    """Public reads stay anonymous; writes use ``require_actor``."""
    claims = await get_current_user(request)
    if not claims:
        return None
    user_id = _claim_user_id(claims)
    if not user_id:
        return None
    return Actor(user_id=user_id, role=str(claims.get("role") or "USER"))


async def require_actor(actor: Actor | None = Depends(get_optional_actor)) -> Actor:
    """Authenticated caller.

    Declared as a dependency over ``get_optional_actor`` so a test substitutes
    identity through one override instead of monkeypatching the auth module.
    """
    if actor is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return actor


async def get_store(db: AsyncSession = Depends(get_db)) -> EngagementStore:
    return SqlEngagementStore(db)
