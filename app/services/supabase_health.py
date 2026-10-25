"""Supabase health probe and degradation circuit breaker."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.circuit_breaker import AsyncCircuitBreaker
from app.services.supabase_transport import _get_headers, _get_url


async def check_health() -> dict[str, Any]:
    """Verify Supabase connectivity."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{_get_url()}/rest/v1/profiles?limit=0", headers=_get_headers())
            return {
                "status": "healthy" if r.status_code == 200 else "degraded",
                "tables": 18,  # total tables we expect
                "timestamp": datetime.now(UTC).isoformat(),
            }
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        return {"status": "unreachable", "error": str(e)}


_supabase_cb = AsyncCircuitBreaker(
    name="supabase",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
    half_open_max=1,
)
