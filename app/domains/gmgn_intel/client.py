"""Raw GMGN Agent API reads for the intelligence layer.

Deliberately thin: it borrows the provider's transport seam (``_request``,
which already handles proxy rotation and retries) and returns *raw* payloads.
Parsing stays in our normalizers because the provider's own transforms
flatten trenches to one bucket and stop one level short on token info.

Auth (verified live 2026-10-24): ``X-APIKEY`` header plus ``timestamp`` and
``client_id`` query params; a reused (timestamp, client_id) pair is rejected
with 401, so both are regenerated per request.

Budget (verified live 2026-10-24): the free tier allows **5 requests per
window** and answers 429 ``RATE_LIMIT_BANNED`` with ``x-ratelimit-reset``.
Every extra request inside a ban *extends* it, so this client queues for a
slot instead of retrying, and parks until the advertised reset on a 429.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import deque
from typing import Any

from app.core.logging import get_logger
from app.domains.gmgn_intel.coerce import gmgn_chain

log = get_logger(__name__)

BASE_URL = "https://openapi.gmgn.ai"
ACCEPT = {"accept": "application/json"}
MAX_CALLS_PER_WINDOW = int(os.environ.get("GMGN_INTEL_MAX_CALLS", "5"))
WINDOW_SECONDS = float(os.environ.get("GMGN_INTEL_WINDOW_SECONDS", "60"))
BUFFER_SECONDS = 1.5
# Free tier is 5 calls/window, so repeated reads must not spend a slot twice.
CACHE_TTL = {"trenches": 20.0, "rank": 60.0, "info": 120.0, "security": 300.0}


def _reset_at(resp: Any, payload: Any) -> float | None:
    """Advertised reset time (unix seconds) from headers or the JSON body."""
    raw = resp.headers.get("x-ratelimit-reset") or resp.headers.get("X-RateLimit-Reset")
    body = payload if isinstance(payload, dict) else {}
    candidate = raw or body.get("reset_at")
    try:
        return float(candidate) if candidate else None
    except (TypeError, ValueError):
        return None


class GMGNIntelClient:
    """Thin, self-throttling async reader over the GMGN Agent API."""

    def __init__(self, provider: Any, base_url: str = BASE_URL) -> None:
        self._provider = provider
        self._base = base_url.rstrip("/")
        self._slots: deque[float] = deque()
        self._blocked_until = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}

    def _params(self, chain: str, **extra: Any) -> dict[str, Any]:
        """Fresh replay-guard params for one call (never reused)."""
        params: dict[str, Any] = {
            "chain": gmgn_chain(chain),
            "timestamp": int(time.time()),
            "client_id": str(uuid.uuid4()),
        }
        params.update({key: value for key, value in extra.items() if value is not None})
        return params

    def _headers(self) -> dict[str, str]:
        key = getattr(self._provider, "api_key", "") or ""
        return {"X-APIKEY": key, **ACCEPT}

    async def _acquire(self) -> None:
        """Wait for a request slot — never spend a call inside a ban window."""
        while True:
            now = time.time()
            if now < self._blocked_until:
                await asyncio.sleep(self._blocked_until - now + BUFFER_SECONDS)
                continue
            while self._slots and now - self._slots[0] > WINDOW_SECONDS:
                self._slots.popleft()
            if len(self._slots) < MAX_CALLS_PER_WINDOW:
                self._slots.append(now)
                return
            await asyncio.sleep(WINDOW_SECONDS - (now - self._slots[0]) + 0.2)

    async def _call(
        self, method: str, path: str, chain: str, extra: dict[str, Any], body: dict[str, Any] | None
    ) -> Any:
        """One rate-limited request; params are minted *after* the slot is granted."""
        key = f"{method}:{path}:{chain}:{sorted(extra.items())}"
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL.get(path.rsplit("/", 1)[-1], 30.0):
            return cached[1]
        await self._acquire()
        params = self._params(chain, **extra)
        resp = await self._provider._request(
            method, f"{self._base}{path}", headers=self._headers(), params=params, json=body
        )
        payload: Any = None
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if resp.status_code == 429:
            reset = _reset_at(resp, payload)
            self._blocked_until = max(reset or 0.0, time.time() + WINDOW_SECONDS)
            log.debug("gmgn intel: 429 on %s, parked until %s", path, self._blocked_until)
            return None
        if resp.status_code != 200:
            log.debug("gmgn intel: HTTP %s on %s", resp.status_code, path)
            return None
        if not isinstance(payload, dict) or payload.get("code") != 0:
            log.debug("gmgn intel: code %s on %s", (payload or {}).get("code"), path)
            return None
        self._cache[key] = (time.time(), payload.get("data"))
        return payload.get("data")

    async def trenches(self, chain: str, limit: int = 60) -> dict[str, Any] | None:
        """All three launchpad buckets in one POST /v1/trenches call."""
        sections = {}
        for bucket in ("new_creation", "near_completion", "completed"):
            sections[bucket] = {
                "filters": ["offchain", "onchain"],
                "launchpad_platform_v2": True,
                "limit": max(1, min(int(limit), 80)),
            }
        data = await self._call("POST", "/v1/trenches", chain, {}, {"version": "v2", **sections})
        return data if isinstance(data, dict) else None

    async def rank(self, chain: str, interval: str = "1h", limit: int = 50) -> list[dict[str, Any]] | None:
        """GET /v1/market/rank — GMGN's order is input only, never our output order."""
        extra = {"interval": interval, "order_by": "volume", "direction": "desc", "limit": limit}
        data = await self._call("GET", "/v1/market/rank", chain, extra, None)
        if not isinstance(data, dict):
            return None
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        rows = inner.get("rank") or inner.get("list") or []
        return rows if isinstance(rows, list) else None

    async def token_info(self, address: str, chain: str) -> dict[str, Any] | None:
        """GET /v1/token/info — the token object sits directly under ``data``."""
        data = await self._call("GET", "/v1/token/info", chain, {"address": address}, None)
        return data if isinstance(data, dict) else None

    async def token_security(self, address: str, chain: str) -> dict[str, Any] | None:
        """GET /v1/token/security — honeypot/taxes/concentration facts."""
        data = await self._call("GET", "/v1/token/security", chain, {"address": address}, None)
        return data if isinstance(data, dict) else None
