"""Proxy-enabled HTTP fetcher for 429/403-blocked news sources.

Targets the sources flagged ``DEAD`` in ``news_sources_status.json`` with
HTTP 429 (reddit-*) and HTTP 403 (coingecko-buzz / defillama / messari /
*-substack) — plain direct requests no longer work for them.

Escalation ladder (stops at the first success):
  1. Direct httpx with a browser User-Agent
  2. FlareSolverr (default http://127.0.0.1:8192/v1) — Cloudflare/WAF bypass
  3. Residential HTTP proxy (``WEBSHARE_PROXY_URL`` / ``WEBSHARE_PROXY_LIST``)
     for 429/403 rate-limited hosts — egress IP rotation
  4. Crawl4AI headless browser — last resort (lazy import; requires
     ``crawl4ai`` + Playwright chromium, same as ``app/rag/crawler.py``)

Configuration is fully env-driven; no credentials are hardcoded:

   WEBSHARE_PROXY_URL      single proxy     http://user:pass@host:port
   WEBSHARE_PROXY_LIST     rotation list    space/newline/comma separated URLs
                         (falls back to gopass rmi/infra/webshare_proxy_list
                          when unset — see app/databus/webshare_proxies.py)
   FLARESOLVERR_URL        default          http://127.0.0.1:8192/v1
   PROXY_SCRAPER_ENABLED   master switch    default "1"
   PROXY_SCRAPER_ALLOW_CRAWL4AI  last-resort toggle, default "1"
   PROXY_SCRAPER_MAX_BYTES cap response body, default 2_000_000
   PROXY_SCRAPER_TIMEOUT_DIRECT / _FLARE / _PROXY  seconds, 20/60/45

The proxy credentials are NOT committed to the repo — the module reads them
from the environment, or from gopass (``rmi/infra/webshare_proxy_list``) via
:mod:`app.databus.webshare_proxies`, at call time and the proxy tier is
skipped (with a log line) when none are available, so direct / FlareSolverr /
Crawl4AI still work.

Proxy URLs are redacted (userinfo stripped) everywhere they are logged.
"""

from __future__ import annotations

import asyncio
import itertools
import os
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

import httpx

from app.core.logging import get_logger
from app.databus.webshare_proxies import load_proxies

log = get_logger(__name__)

# ── Env-driven defaults ───────────────────────────────────────────────────────

FLARESOLVERR_URL: Final[str] = os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8192/v1")
PROXY_SCRAPER_ENABLED: Final[bool] = os.getenv("PROXY_SCRAPER_ENABLED", "1").lower() not in {"0", "false", "no"}
PROXY_SCRAPER_ALLOW_CRAWL4AI: Final[bool] = os.getenv("PROXY_SCRAPER_ALLOW_CRAWL4AI", "1").lower() not in {
    "0",
    "false",
    "no",
}
PROXY_SCRAPER_MAX_BYTES: Final[int] = int(os.getenv("PROXY_SCRAPER_MAX_BYTES", "2000000"))
PROXY_SCRAPER_TIMEOUT_DIRECT: Final[float] = float(os.getenv("PROXY_SCRAPER_TIMEOUT_DIRECT", "20"))
PROXY_SCRAPER_TIMEOUT_FLARE: Final[float] = float(os.getenv("PROXY_SCRAPER_TIMEOUT_FLARE", "60"))
PROXY_SCRAPER_TIMEOUT_PROXY: Final[float] = float(os.getenv("PROXY_SCRAPER_TIMEOUT_PROXY", "45"))

# Status codes that mean "try a different egress path" (rate limit / WAF).
ESCALATE_STATUS: Final[frozenset[int]] = frozenset({403, 429, 401, 503})

# Marker substrings for WAF challenge pages served with a 200 (Cloudflare
# "Just a moment", PerimeterX/DataDome variants). Only trusted on small bodies.
_WAF_MARKERS: Final[tuple[str, ...]] = (
    "cf-chl-",
    "challenge-platform",
    "just a moment",
    "attention required",
    "enable javascript and cookies",
    "verify you are human",
    "checking your browser",
)

_UA_POOL: Final[tuple[str, ...]] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
)

_BASE_HEADERS: Final[dict[str, str]] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


@dataclass
class FetchResult:
    """Outcome of a proxied fetch."""

    url: str
    ok: bool
    content: str = ""
    status_code: int | None = None
    method: str = ""  # winner tier: direct | flaresolverr | proxy | crawl4ai
    error: str = ""
    ladder: list[str] = field(default_factory=list)  # every tier attempted


def _redact(proxy_url: str) -> str:
    """Strip ``user:pass@`` from a proxy URL so credentials never hit logs."""
    try:
        parts = urlsplit(proxy_url)
    except ValueError:
        return "<invalid-proxy-url>"
    if parts.username is None and parts.password is None:
        return proxy_url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    scheme = parts.scheme or "http"
    return f"{scheme}://{host}"


def _is_waf_block(status: int | None, body: str) -> bool:
    """Heuristic for WAF challenge pages served with a 200 status."""
    if status in ESCALATE_STATUS:
        return True
    if status != 200:
        return False
    if len(body) > 50_000:
        return False  # a real page, not a challenge stub
    lowered = body.lower()
    return any(marker in lowered for marker in _WAF_MARKERS)


class ProxyScraper:
    """Async fetcher with a four-tier escalation ladder.

    Build once per process (or per sweep); ``fetch()`` is safe to call
    concurrently. Proxy egress is rotated round-robin across the list from
    ``WEBSHARE_PROXY_LIST`` / ``WEBSHARE_PROXY_URL`` (gopass
    ``rmi/infra/webshare_proxy_list`` fallback via
    :func:`app.databus.webshare_proxies.load_proxies`).
    """

    def __init__(
        self,
        flare_url: str | None = None,
        proxies: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        self._flare_url = flare_url or FLARESOLVERR_URL
        self._enabled = enabled and PROXY_SCRAPER_ENABLED
        # Resolve egress proxies at init; credentials come from env / gopass.
        if proxies is None:
            proxies = load_proxies()
        self._proxies = proxies
        self._proxy_cycle = itertools.cycle(proxies) if proxies else None
        self._ua_cycle = itertools.cycle(_UA_POOL)

    def _next_proxy(self) -> str | None:
        return next(self._proxy_cycle) if self._proxy_cycle else None

    def _headers(self) -> dict[str, str]:
        return {**_BASE_HEADERS, "User-Agent": next(self._ua_cycle)}

    # ── Public API ─────────────────────────────────────────────────────────

    async def fetch(self, url: str) -> FetchResult:
        """Fetch ``url``, escalating through the ladder until one tier works."""
        if not self._enabled:
            return FetchResult(url=url, ok=False, error="proxy scraper disabled (PROXY_SCRAPER_ENABLED=0)")

        # 1) Direct with a browser UA.
        content, status = await self._fetch_direct(url)
        if content is not None and not _is_waf_block(status, content):
            return FetchResult(url=url, ok=True, content=content, status_code=status, method="direct")

        ladder = ["direct"]
        direct_hint = f"HTTP {status}" if status is not None else "request error"

        # 2) FlareSolverr — Cloudflare/WAF bypass.
        content, status = await self._fetch_flaresolverr(url)
        if content is not None:
            ladder.append("flaresolverr")
            return FetchResult(url=url, ok=True, content=content, status_code=status, method="flaresolverr")

        # 3) Residential proxy — 429/403 rate-limit escape (rotating egress).
        if self._proxy_cycle is not None:
            content, status = await self._fetch_via_proxy(url)
            if content is not None:
                ladder.append("proxy")
                return FetchResult(url=url, ok=True, content=content, status_code=status, method="proxy")
            ladder.append("proxy")

        # 4) Crawl4AI headless — last resort.
        if PROXY_SCRAPER_ALLOW_CRAWL4AI:
            content = await self._fetch_crawl4ai(url)
            if content is not None:
                ladder.append("crawl4ai")
                return FetchResult(url=url, ok=True, content=content, status_code=None, method="crawl4ai")

        errors: list[str] = [f"direct {direct_hint}"]
        if self._proxy_cycle is None:
            errors.append(
                "proxy not configured (set WEBSHARE_PROXY_URL / WEBSHARE_PROXY_LIST or gopass rmi/infra/webshare_proxy_list)"
            )
        return FetchResult(
            url=url,
            ok=False,
            status_code=status,
            error="; ".join(errors),
            ladder=ladder,
        )

    # ── Tiers ──────────────────────────────────────────────────────────────

    async def _fetch_direct(self, url: str) -> tuple[str | None, int | None]:
        try:
            async with httpx.AsyncClient(timeout=PROXY_SCRAPER_TIMEOUT_DIRECT, follow_redirects=True) as client:
                resp = await client.get(url, headers=self._headers())
            body = resp.content[:PROXY_SCRAPER_MAX_BYTES].decode("utf-8", errors="replace")
            return body, resp.status_code
        except httpx.HTTPError as e:
            log.debug("proxy_scraper_direct_failed", url=url, error=str(e)[:120])
            return None, None

    async def _fetch_flaresolverr(self, url: str) -> tuple[str | None, int | None]:
        """POST ``{"cmd": "request.get", ...}`` to the FlareSolverr /v1 API."""
        try:
            payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
            async with httpx.AsyncClient(timeout=PROXY_SCRAPER_TIMEOUT_FLARE) as client:
                resp = await client.post(self._flare_url, json=payload, headers=self._headers())
                if not resp.is_success:
                    log.debug("proxy_scraper_flare_http", url=url, status=resp.status_code)
                    return None, resp.status_code
                data = resp.json()
            solution = data.get("solution") or {}
            status = solution.get("status")
            response = solution.get("response")
            if isinstance(response, str) and response.strip():
                return response[:PROXY_SCRAPER_MAX_BYTES], int(status) if status else 200
            log.debug("proxy_scraper_flare_empty", url=url, solution_keys=list(solution.keys())[:5])
            return None, int(status) if status else None
        except (httpx.HTTPError, ValueError) as e:
            log.debug("proxy_scraper_flare_failed", url=url, error=str(e)[:120])
            return None, None

    async def _fetch_via_proxy(self, url: str) -> tuple[str | None, int | None]:
        """Fetch through a rotating residential proxy. Fails fast when none set."""
        proxy_url = self._next_proxy()
        if proxy_url is None:
            log.info(
                "proxy_scraper_no_proxy",
                hint="set WEBSHARE_PROXY_URL / WEBSHARE_PROXY_LIST or gopass rmi/infra/webshare_proxy_list",
            )
            return None, None
        try:
            async with httpx.AsyncClient(
                timeout=PROXY_SCRAPER_TIMEOUT_PROXY, follow_redirects=True, proxy=proxy_url
            ) as client:
                resp = await client.get(url, headers=self._headers())
            body = resp.content[:PROXY_SCRAPER_MAX_BYTES].decode("utf-8", errors="replace")
            if resp.is_success:
                return body, resp.status_code
            log.debug(
                "proxy_scraper_proxy_http",
                url=url,
                status=resp.status_code,
                proxy=_redact(proxy_url),
            )
            return None, resp.status_code
        except httpx.HTTPError as e:
            log.debug(
                "proxy_scraper_proxy_failed",
                url=url,
                error=str(e)[:120],
                proxy=_redact(proxy_url),
            )
            return None, None

    async def _fetch_crawl4ai(self, url: str) -> str | None:
        """Last resort: headless browser extraction (lazy import, may be absent)."""
        try:
            from app.rag.crawler import crawl_crypto_page

            page = await crawl_crypto_page(url)
            content = str(page.get("content") or "")
            return content[:PROXY_SCRAPER_MAX_BYTES] if content.strip() else None
        except Exception as e:  # any crawl failure degrades to None
            log.debug("proxy_scraper_crawl4ai_failed", url=url, error=str(e)[:120])
            return None


# ── Module-level convenience (singleton-ish, env-configured) ─────────────────


_default_scraper: ProxyScraper | None = None


def get_scraper() -> ProxyScraper:
    """Return the process-wide default :class:`ProxyScraper` (lazy, env-driven)."""
    global _default_scraper  # deliberate lazy singleton
    if _default_scraper is None:
        _default_scraper = ProxyScraper()
    return _default_scraper


async def fetch(url: str) -> FetchResult:
    """Fetch ``url`` with the default env-configured ladder."""
    return await get_scraper().fetch(url)


def fetch_sync(url: str) -> FetchResult:
    """Blocking convenience wrapper for scripts / cron (runs its own loop)."""
    return asyncio.run(fetch(url))


def _cli(url: str) -> None:
    """Minimal CLI: ``python -m app.databus.proxy_scraper <url>``."""
    import json

    result = fetch_sync(url)
    print(json.dumps({**result.__dict__, "content": result.content[:500]}, indent=2, default=str))


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m app.databus.proxy_scraper <url>")
        raise SystemExit(2)
    _cli(sys.argv[1])
