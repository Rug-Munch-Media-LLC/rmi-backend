"""Our own indexed token metadata (``tokens.extra``) as an icon source."""

from __future__ import annotations

from typing import Any

from app.token_icons.chains import chain_slugs

_KEYS = ("image", "image_url", "imageUrl", "logo", "logo_url", "logoUrl", "icon", "icon_url")


async def fetch_icon(client: Any, chain: str, token: str) -> str | None:
    """Read a cached logo URL from our Postgres token metadata (no network)."""
    del client  # source is local by design
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.token import Token

    chain_id, _ = chain_slugs(chain)
    candidates = {str(chain).strip().lower(), chain_id} - {""}
    async with async_session_factory() as session:
        result = await session.execute(
            select(Token.extra).where(
                Token.chain.in_(tuple(candidates)), Token.address == token
            ).limit(1)
        )
        extra = result.scalar_one_or_none()
    if not isinstance(extra, dict):
        return None
    for key in _KEYS:
        value = extra.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
