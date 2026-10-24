"""CoinGecko contract-metadata icon source (free tier; 429-tolerant)."""

from __future__ import annotations

import itertools
import os
from typing import Any

from app.token_icons.chains import chain_slugs

_ENDPOINT = "https://api.coingecko.com/api/v3/coins/{platform}/contract/{token}"
_TIMEOUT = 6.0
_CURSOR = itertools.count()


async def fetch_icon(client: Any, chain: str, token: str) -> str | None:
    """Return ``image.large``/``image.small`` from CoinGecko contract metadata."""
    _, platform = chain_slugs(chain)
    if not platform:
        return None
    keys: list[str | None] = _api_keys() or [None]
    start = next(_CURSOR) % len(keys)
    for key in keys[start:] + keys[:start]:
        headers = {"x-cg-demo-api-key": key} if key else {}
        response = await client.get(
            _ENDPOINT.format(platform=platform, token=token),
            timeout=_TIMEOUT,
            headers=headers,
        )
        if response.status_code in (401, 402, 403, 429):
            continue
        if response.status_code != 200:
            return None
        payload = response.json()
        image = payload.get("image") if isinstance(payload, dict) else None
        if not isinstance(image, dict):
            return None
        return image.get("large") or image.get("small") or None
    return None


def _api_keys() -> list[str]:
    """Free/demo keys, comma-separated; rotate on 401/402/403/429."""
    pool = os.getenv("COINGECKO_API_KEYS", "").strip()
    keys = [part.strip() for part in pool.split(",") if part.strip()]
    if keys:
        return keys
    single = os.getenv("COINGECKO_API_KEY", "").strip()
    return [single] if single else []
