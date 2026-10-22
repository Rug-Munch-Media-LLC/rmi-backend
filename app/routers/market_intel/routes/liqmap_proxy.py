"""Residential-proxy pool handling for the market-intel liquidation venues.

Why this is its own module: the venue clients in ``routes/hyperliquid.py``
parse exchange payloads, while this module owns the POOL - parsing the proxy
list, rotating over it, and remembering which exits are useless. Two
concerns, two modules, and the venue module stays small.

Why a pool at all: Bybit and Binance refuse our own egress (HTTP 403 and
HTTP 451 "Service unavailable from a restricted location") and also refuse a
US exit, which is where our egress and most of the pool live. A non-US pool
exit answers both. Measured over the whole pool on 2026-10-21: 8 of 20
entries answer 200, 8 are US and refused, 4 are unreachable.

A refusal is a dead exit FOR THE GEO-BLOCKED VENUES, not a broken proxy -
the same exit answers OKX fine. So the failures are remembered here and the
rotation converges on the exits that answer, instead of re-testing the same
dead ones forever.

One call, several exits: when only some exits sit in a country the venue
accepts, a single-exit call reports the venue as offline on every poll that
lands on a refused exit. :func:`fetch_geo_venue` therefore tries a bounded
number of candidates per call, so ONE poll converges on an exit that
answers, and the exits that refused are remembered for the next poll.
"""

from __future__ import annotations

import os
from typing import Any, Final

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

#: HTTP status a geo-blocked venue returns to a refused exit: Bybit 403,
#: Binance 451 ("Service unavailable from a restricted location").
VENUE_GEO_REFUSALS: Final[frozenset[int]] = frozenset({403, 451})

#: How many pool exits one venue call may try before the venue is offline.
POOL_TRIES: Final[int] = 8

#: Timeout for a single venue request, in seconds.
VENUE_TIMEOUT_S: Final[float] = 12.0

_failed_proxies: set[str] = set()
_pool_cache: list[str] = []
_pool_source: str = ""
_pool_index: int = 0


def proxy_pool() -> list[str]:
    """The residential pool as a list of proxy urls (cached per process).

    Parsed by the shared rotator's parser, so the same comma/space/newline
    list format and the same credential handling apply. An empty pool means
    "direct".

    The cache is keyed on the RAW env value, not just on being empty, so a
    pool refreshed by webshare-sync.py replaces the stale list instead of
    being ignored for the rest of the process' life.
    """
    global _pool_cache, _pool_source
    raw = os.getenv("WEBSHARE_PROXY_LIST", "")
    if raw != _pool_source:
        from app.databus.proxy_rotator import parse_proxy_list

        _pool_cache = parse_proxy_list(raw)
        _pool_source = raw
    return _pool_cache


def pick_proxy() -> str | None:
    """Next pool entry that has not failed for a geo-blocked venue.

    A monotonic cursor over the ORIGINAL pool order, skipping the exits
    already known to fail. Indexing a filtered list instead would re-test
    failed entries, because the modulus changes as the filter grows.

    When every exit has failed at least once the set is cleared and the pool
    is re-tested from the start: refusals are a geo property of the exit, but
    the pool is refreshed periodically by webshare-sync.py, so a refreshed
    entry must get a fresh chance.
    """
    global _pool_index
    pool = proxy_pool()
    if not pool:
        return None
    for _ in range(len(pool)):
        proxy = pool[_pool_index % len(pool)]
        _pool_index += 1
        if proxy not in _failed_proxies:
            return proxy
    _failed_proxies.clear()
    _pool_index = 0
    return pool[0]


def note_failure(proxy: str | None) -> None:
    """Mark an exit as unusable for the geo-blocked venues and cool it down.

    Called for a geo refusal AND for a transport failure - an unreachable
    exit is just as useless as a refused one, and it is the reason a rotation
    that only recorded refusals never converged.
    """
    if proxy is None:
        return
    _failed_proxies.add(proxy)
    from app.databus.proxy_rotator import get_proxy_rotator

    get_proxy_rotator().mark_failed(proxy)


def note_refusal(proxy: str | None, status_code: int) -> None:
    """Remember that this exit is refused by the geo-blocked venues.

    A 403/451 means the exit's country is refused. The transport-failure
    path goes through :func:`note_failure` directly.
    """
    if proxy is None or status_code not in VENUE_GEO_REFUSALS:
        return
    note_failure(proxy)


def pool_candidates(tries: int = POOL_TRIES) -> list[str | None]:
    """Up to `tries` distinct pool exits to try, in pool order.

    An empty pool means "direct", so the list holds a single None and the
    caller makes one direct attempt.
    """
    if not proxy_pool():
        return [None]
    out: list[str | None] = []
    for _ in range(max(1, tries)):
        proxy = pick_proxy()
        if proxy is None or proxy in out:
            break
        out.append(proxy)
    return out or [None]


async def _get_with_proxy(
    url: str,
    params: dict[str, Any] | None,
    proxy: str | None,
) -> httpx.Response | None:
    """One GET through one exit; None when the exit is unreachable."""
    try:
        async with httpx.AsyncClient(timeout=VENUE_TIMEOUT_S, proxy=proxy) as client:
            return await client.get(url, params=params)
    except Exception:
        return None


async def fetch_geo_venue(
    url: str,
    params: dict[str, Any] | None = None,
    tries: int = POOL_TRIES,
) -> tuple[httpx.Response | None, str | None]:
    """GET a geo-blocked venue through the pool, trying exits until one answers.

    Returns the first answer that is not a geo refusal, plus the exit that
    produced it (None when the request went direct). A refusal and an
    unreachable exit both move on to the next candidate; when every candidate
    is spent the venue is reported as offline.
    """
    for proxy in pool_candidates(tries):
        response = await _get_with_proxy(url, params, proxy)
        if response is None:
            note_failure(proxy)
            continue
        if response.status_code in VENUE_GEO_REFUSALS:
            note_refusal(proxy, response.status_code)
            log.warning("liqmap venue refused exit (status=%s)", response.status_code)
            continue
        return response, proxy
    return None, None
