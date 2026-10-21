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
"""

from __future__ import annotations

import os
from typing import Final

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

#: HTTP status a geo-blocked venue returns to a refused exit: Bybit 403,
#: Binance 451 ("Service unavailable from a restricted location").
VENUE_GEO_REFUSALS: Final[frozenset[int]] = frozenset({403, 451})

#: Pool exits that failed for a geo-blocked venue. Two ways an exit fails
#: here, and BOTH are properties of the exit rather than of the venue:
#:   - HTTP 403/451: the exit's country is refused by the venue.
#:   - a transport error: the exit is unreachable.
#: Recording only the HTTP refusals re-tested the unreachable ones forever.
_failed_proxies: set[str] = set()
_pool_cache: list[str] = []
_pool_index: int = 0


def proxy_pool() -> list[str]:
    """The residential pool as a list of proxy urls (cached per process).

    Parsed by the shared rotator's parser, so the same comma/space/newline
    list format and the same credential handling apply. An empty pool means
    "direct".
    """
    global _pool_cache
    if not _pool_cache:
        from app.databus.proxy_rotator import parse_proxy_list

        _pool_cache = parse_proxy_list(os.getenv("WEBSHARE_PROXY_LIST", ""))
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


def proxy_client(timeout: float = 12.0) -> tuple[httpx.AsyncClient, str | None]:
    """A client for a geo-blocked venue, plus the proxy it was given.

    The proxy is returned (None when direct) so the caller can report a
    refusal back here. The pool comes from the standard ``WEBSHARE_PROXY_LIST``
    env; an empty list means "direct", so an unconfigured deployment behaves
    exactly as before.
    """
    proxy = pick_proxy()
    return httpx.AsyncClient(timeout=timeout, proxy=proxy), proxy
