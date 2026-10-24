"""DexScreener token icon source (free, keyless, covers every supported chain)."""

from __future__ import annotations

from typing import Any

from app.token_icons.chains import chain_slugs

_ENDPOINT = "https://api.dexscreener.com/latest/dex/tokens/{token}"
_TIMEOUT = 6.0


async def fetch_icon(client: Any, chain: str, token: str) -> str | None:
    """Return ``info.imageUrl`` for the first pair on the requested chain."""
    chain_id, _ = chain_slugs(chain)
    if not chain_id:
        return None
    response = await client.get(_ENDPOINT.format(token=token), timeout=_TIMEOUT)
    if response.status_code != 200:
        return None
    payload = response.json()
    pairs = payload.get("pairs") if isinstance(payload, dict) else None
    for pair in pairs or []:
        if str(pair.get("chainId", "")).lower() != chain_id:
            continue
        image_url = ((pair.get("info") or {}).get("imageUrl")) or None
        if image_url:
            return str(image_url)
    return None
