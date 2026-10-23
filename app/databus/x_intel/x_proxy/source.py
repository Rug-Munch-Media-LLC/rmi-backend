"""WebShare proxy-list source with a TTL cache and an env fallback.

The proxy list is fetched from the WebShare API at runtime, so the fleet no
longer depends on a hand-maintained ``WEBSHARE_PROXY_LIST`` string going
stale. The API key comes from the environment (``WEBSHARE_API_KEY``, sourced
from gopass ``rmi/backend/webshare_api_key``) and is never logged.

Resolution order:

1. in-process TTL cache (``CACHE_TTL_SECONDS``)
2. WebShare API ``/api/v2/proxy/list/`` — only entries marked ``valid``
3. env ``WEBSHARE_PROXY_LIST`` / ``_USER`` / ``_PASS`` (legacy config)
4. empty list — the proxy tier is simply disabled and callers go direct

URL construction is delegated to ``app.databus.proxy_rotator.build_proxy_url``
so credential encoding and scheme handling have a single implementation.
"""

from __future__ import annotations

import os
import time
from typing import Any, Final

import httpx

from app.core.logging import get_logger
from app.databus.proxy_rotator import build_proxy_url, parse_proxy_list

log = get_logger(__name__)

API_URL: Final[str] = "https://proxy.webshare.io/api/v2/proxy/list/"
API_KEY_ENV: Final[str] = "WEBSHARE_API_KEY"
CACHE_TTL_SECONDS: Final[float] = 300.0
PAGE_SIZE: Final[int] = 100
MAX_PAGES: Final[int] = 5
API_TIMEOUT: Final[float] = 20.0

_cache: tuple[float, list[str]] | None = None


async def get_proxy_candidates(*, force: bool = False) -> list[str]:
    """Return ready-to-use proxy URLs (API first, env fallback, else empty).

    ``force`` bypasses the TTL cache (used by the CLI probe and by tests).
    Errors never propagate: a failed API call degrades to the env list and
    then to an empty list, which callers treat as "proxy tier disabled".
    """
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return list(_cache[1])

    api_key = os.getenv(API_KEY_ENV, "").strip()
    proxies: list[str] = []
    source = "none"

    if api_key:
        url: str | None = API_URL
        page = 0
        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
                while url and page < MAX_PAGES:
                    page += 1
                    params = {"mode": "direct", "page": page, "page_size": PAGE_SIZE} if page == 1 else None
                    resp = await client.get(
                        url,
                        params=params,
                        headers={"Authorization": f"Token {api_key}"},
                    )
                    if resp.status_code != 200:
                        log.warning("webshare_list_http_error", status=resp.status_code, page=page)
                        break
                    payload: Any = resp.json()
                    if not isinstance(payload, dict):
                        log.warning("webshare_list_bad_payload", page=page)
                        break
                    for item in payload.get("results") or []:
                        if not isinstance(item, dict) or not item.get("valid"):
                            continue
                        host = str(item.get("proxy_address") or "").strip()
                        port = item.get("port")
                        if not host or not isinstance(port, int):
                            continue
                        built = build_proxy_url(
                            f"{host}:{port}",
                            str(item.get("username") or ""),
                            str(item.get("password") or ""),
                        )
                        if built is not None:
                            proxies.append(built)
                    nxt = payload.get("next")
                    url = nxt if isinstance(nxt, str) and nxt else None
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            log.warning("webshare_list_failed", error=type(exc).__name__)
            proxies = []
        if proxies:
            source = "webshare_api"
        else:
            log.warning("webshare_api_empty_or_failed")

    if not proxies:
        proxies = parse_proxy_list(
            os.getenv("WEBSHARE_PROXY_LIST", ""),
            os.getenv("WEBSHARE_PROXY_USER", ""),
            os.getenv("WEBSHARE_PROXY_PASS", ""),
        )
        source = "env" if proxies else "none"

    _cache = (now, list(proxies))
    log.info("proxy_candidates_resolved", count=len(proxies), source=source)
    return list(proxies)
