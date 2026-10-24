"""Resolve one token icon: cache -> providers -> validation -> cache.

Never raises for upstream failure and never returns a URL that is not
``https`` (or the deterministic local fallback).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger
from app.token_icons.cache import POSITIVE_TTL, IconCache, get_icon_cache
from app.token_icons.fallback import fallback_icon_url
from app.token_icons.probe import image_content_ok
from app.token_icons.providers import PROVIDERS
from app.token_icons.urls import acceptable_icon_url

logger = get_logger("token_icons.resolver")

_LOCK_LIMIT = 256
_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    if len(_LOCKS) >= _LOCK_LIMIT:
        _LOCKS.pop(next(iter(_LOCKS)), None)
    return _LOCKS.setdefault(key, asyncio.Lock())


def _fallback(chain: str, token: str, *, cached: bool = False) -> dict[str, Any]:
    return {
        "chain": chain,
        "token": token,
        "icon_url": fallback_icon_url(chain, token),
        "source": "fallback",
        "cached": cached,
        "fallback": True,
        "content_type": None,
        "expires_in": 0,
    }


async def _probe_sources(client: httpx.AsyncClient, chain: str, token: str) -> dict[str, Any] | None:
    """Try each provider; return the first URL that passes URL + content-type checks."""
    for name, fetch in PROVIDERS:
        try:
            candidate = await fetch(client, chain, token)
        except Exception as exc:  # one broken source never blocks the others
            logger.debug("icon source %s failed for %s:%s — %s", name, chain, token, exc)
            continue
        if not acceptable_icon_url(candidate):
            continue
        content_type = await image_content_ok(client, str(candidate))
        if not content_type:
            logger.debug("icon source %s rejected by content-type for %s:%s", name, chain, token)
            continue
        return {
            "icon_url": str(candidate).strip(),
            "source": name,
            "content_type": content_type,
        }
    return None


async def resolve_icon(
    chain: str,
    token: str,
    *,
    client: httpx.AsyncClient | None = None,
    cache: IconCache | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Resolve an icon for ``(chain, token)``; always returns a usable URL."""
    chain_key = str(chain or "").strip().lower()
    token_key = str(token or "").strip()
    if not chain_key or not token_key:
        return _fallback(chain_key or "unknown", token_key or "unknown")
    store = cache or get_icon_cache()
    if not refresh:
        cached = await store.get(chain_key, token_key)
        if cached:
            return {**cached, "cached": True, "fallback": False, "expires_in": POSITIVE_TTL}
    async with _lock_for(f"{chain_key}:{token_key}"):
        if not refresh:
            cached = await store.get(chain_key, token_key)  # another waiter filled it
            if cached:
                return {**cached, "cached": True, "fallback": False, "expires_in": POSITIVE_TTL}
        if not refresh and await store.is_missing(chain_key, token_key):
            return _fallback(chain_key, token_key, cached=True)
        owned = client is None
        http_client = client or httpx.AsyncClient(timeout=8.0, follow_redirects=True)
        try:
            hit = await _probe_sources(http_client, chain_key, token_key)
        finally:
            if owned:
                await http_client.aclose()
        if hit is None:
            await store.mark_missing(chain_key, token_key)
            return _fallback(chain_key, token_key)
        entry = {"chain": chain_key, "token": token_key, **hit}
        await store.put(chain_key, token_key, entry)
        return {**entry, "cached": False, "fallback": False, "expires_in": POSITIVE_TTL}
