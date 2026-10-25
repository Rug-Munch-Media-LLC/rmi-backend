"""Read-only access to the scanner's cached result — never a scan trigger.

The key, the TTL and the JSON shape are the scanner's own contract
(``sentinelscan:{chain}:{address}``, one hour). A public read only ever looks
at what that cache already holds, so an anonymous caller cannot cause work.
"""

from __future__ import annotations

import json
from collections.abc import Mapping  # noqa: TC003 — runtime return annotation
from typing import Any, Protocol

from app.domains.scanner.engines._exceptions import get_redis_async

CACHE_KEY = "sentinelscan:{chain}:{address}"


class PublicScanSource(Protocol):
    """The one read a public route is allowed to perform."""

    async def read(self, chain: str, address: str) -> Mapping[str, Any] | None: ...


class RedisScanCache:
    """Scanner result cache. Missing key, outage or bad JSON all read as None."""

    async def read(self, chain: str, address: str) -> Mapping[str, Any] | None:
        try:
            client = get_redis_async()
            if client is None:
                return None
            raw = await client.get(CACHE_KEY.format(chain=chain, address=address))
            if not raw:
                return None
            payload = json.loads(raw)
        except (ValueError, TypeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None
