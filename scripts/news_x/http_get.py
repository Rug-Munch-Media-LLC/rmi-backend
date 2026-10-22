"""Paced HTTP GET with per-host circuit breaking.

Politeness contract (unchanged from the original fetch module):
  * <= 1 request/second per host, hard, enforced by Limiter
  * 429 -> exponential backoff (5s, 10s, 20s) then the host is parked
  * 3 consecutive failures from one host -> host parked for the rest of the run
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 25.0
MIN_BYTES = 120  # a single pinned post is ~280 bytes
MIN_INTERVAL = 1.0
MAX_ATTEMPTS = 3
PARK_AFTER = 3


@dataclass
class HostState:
    """Per-host pacing + failure bookkeeping for one run."""

    last_call: float = 0.0
    failures: int = 0
    parked: bool = False
    requests: int = 0


class Limiter:
    """One request per host per second, plus parking on repeated failure."""

    def __init__(self, interval: float = MIN_INTERVAL) -> None:
        self.interval = interval
        self.hosts: dict[str, HostState] = {}

    def state(self, url: str) -> HostState:
        """Return (creating) the state for the URL's host."""
        host = (urlparse(url).hostname or "?").lower()
        return self.hosts.setdefault(host, HostState())

    def wait(self, url: str) -> bool:
        """Sleep until the host may be called again; False when parked."""
        state = self.state(url)
        if state.parked:
            return False
        gap = time.monotonic() - state.last_call
        if gap < self.interval:
            time.sleep(self.interval - gap)
        state.last_call = time.monotonic()
        state.requests += 1
        return True

    def fail(self, url: str) -> None:
        """Count a failure; park the host at PARK_AFTER consecutive misses."""
        state = self.state(url)
        state.failures += 1
        if state.failures >= PARK_AFTER:
            state.parked = True

    def ok(self, url: str) -> None:
        """Reset the failure counter after a usable response."""
        self.state(url).failures = 0


def get(
    url: str, limiter: Limiter, proxy: str | None = None, headers: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Paced GET with 429 backoff. Returns (status, body, error)."""
    for attempt in range(MAX_ATTEMPTS):
        if not limiter.wait(url):
            return 0, "", "host_parked"
        try:
            kwargs: dict[str, object] = {
                "headers": {"User-Agent": UA, **(headers or {})},
                "follow_redirects": True,
                "timeout": TIMEOUT,
            }
            if proxy:
                kwargs["proxy"] = proxy
            resp = httpx.get(url, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            limiter.fail(url)
            if attempt + 1 == MAX_ATTEMPTS:
                return 0, "", type(exc).__name__
            continue
        if resp.status_code == 429:
            limiter.fail(url)
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(5.0 * (2**attempt))
            continue
        if resp.status_code >= 400 or len(resp.text) < MIN_BYTES:
            limiter.fail(url)
            return resp.status_code, "", f"http_{resp.status_code}"
        limiter.ok(url)
        return resp.status_code, resp.text, ""
    return 429, "", "rate_limited"


def post_json(
    url: str,
    limiter: Limiter,
    proxy: str | None = None,
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object], str]:
    """Paced POST returning parsed JSON (guest-token activation)."""
    for attempt in range(MAX_ATTEMPTS):
        if not limiter.wait(url):
            return 0, {}, "host_parked"
        try:
            kwargs: dict[str, object] = {
                "headers": {"User-Agent": UA, **(headers or {})},
                "timeout": TIMEOUT,
                "json": payload or {},
            }
            if proxy:
                kwargs["proxy"] = proxy
            resp = httpx.post(url, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            limiter.fail(url)
            if attempt + 1 == MAX_ATTEMPTS:
                return 0, {}, type(exc).__name__
            continue
        if resp.status_code == 429:
            limiter.fail(url)
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(5.0 * (2**attempt))
            continue
        try:
            body = resp.json()
        except ValueError:
            limiter.fail(url)
            return resp.status_code, {}, "bad_json"
        if resp.status_code >= 400:
            limiter.fail(url)
            return resp.status_code, body, f"http_{resp.status_code}"
        limiter.ok(url)
        return resp.status_code, body, ""
    return 429, {}, "rate_limited"
