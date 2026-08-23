"""
Freemium API providers — free tier with API key, limited monthly quotas.

Includes: Birdeye, Solana Tracker, Messari, CoinDesk, Santiment,
VirusTotal, Dune Analytics.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.freemium_apis")

# ── Birdeye (freemium, free tier available) ──

# Verified 2026-08-16 live: the OLD ``/public/price`` / ``/public/token_overview``
# paths return 404 (deprecated). The current paths are ``/defi/price`` and
# ``/defi/token_overview`` (both return 200 with a valid key).
_BIRDEYE_BASE = "https://public-api.birdeye.so"
_BIRDEYE_KEY_ENVS = ("BIRDEYE_API_KEY", "BIRDEYE_API_KEY_ALT")


def _birdeye_keys(primary: str = "") -> list[str]:
    """Ordered Birdeye keys: explicit ``primary`` (from key_env) first, then
    ``BIRDEYE_API_KEY_ALT``. Verified 2026-08-16: the primary key is
    compute-quota-exhausted on Solana (HTTP 400) while the ALT key serves
    both Solana and EVM — providers rotate on 400/401/403. No secrets are
    logged; only key *presence* matters here."""
    import os

    keys: list[str] = []
    for val in (primary, *[os.getenv(env, "") for env in _BIRDEYE_KEY_ENVS]):
        if val and val not in keys:
            keys.append(val)
    return keys


def _birdeye_chain_slug(chain: str) -> str:
    """Normalize a chain name to Birdeye's ``x-chain`` header value.

    ``/defi/price`` and ``/defi/token_overview`` are chain-aware; without
    ``x-chain`` EVM addresses resolve incorrectly (the API returns
    "address is invalid format"). Solana stays the default."""
    c = (chain or "").strip().lower()
    return {
        "eth": "ethereum",
        "mainnet": "ethereum",
        "bnb": "bsc",
        "matic": "polygon",
        "arb": "arbitrum",
        "op": "optimism",
    }.get(c, c or "solana")


async def _birdeye_overview(address: str = "", **kw: Any) -> dict[str, Any] | None:
    """Birdeye — token overview, liquidity, holder stats (Solana/EVM).

    Uses the current ``/defi/token_overview`` path (the old ``/public/*``
    path returns 404) with an ``x-chain`` header and key rotation
    (``BIRDEYE_API_KEY`` → ``BIRDEYE_API_KEY_ALT``) on 400/401/403.
    """
    if not address:
        return None
    chain = _birdeye_chain_slug(kw.get("chain", ""))
    for api_key in _birdeye_keys(kw.get("api_key", "")):
        if not api_key:
            continue
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{_BIRDEYE_BASE}/defi/token_overview",
                    params={"address": address},
                    headers={"x-api-key": api_key, "accept": "application/json", "x-chain": chain},
                )
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in (400, 401, 403):
                    return None  # 404/429/5xx — key is fine, endpoint/rate is not
        except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
            logger.warning("provider error")
    return None


async def _birdeye_price(address: str = "", **kw: Any) -> dict[str, Any] | None:
    """Birdeye — real-time token price (current ``/defi/price`` path).

    Rotates ``BIRDEYE_API_KEY`` → ``BIRDEYE_API_KEY_ALT`` on 400/401/403
    (the primary free-tier compute quota is frequently exhausted on Solana)
    and sends ``x-chain`` so EVM addresses resolve correctly.
    """
    if not address:
        return None
    chain = _birdeye_chain_slug(kw.get("chain", ""))
    for api_key in _birdeye_keys(kw.get("api_key", "")):
        if not api_key:
            continue
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{_BIRDEYE_BASE}/defi/price",
                    params={"address": address},
                    headers={"x-api-key": api_key, "accept": "application/json", "x-chain": chain},
                )
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in (400, 401, 403):
                    return None
        except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
            logger.warning("provider error")
    return None


# ── Solana Tracker (freemium, 5K/mo free quota) ──


async def _solana_tracker_price(mint: str = "", **kw: Any) -> dict[str, Any] | None:
    """Solana Tracker — token price."""
    api_key = kw.get("api_key", "")
    if not mint or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.solanatracker.io/v1/price/{mint}",
                headers={"x-api-key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _solana_tracker_token(mint: str = "", **kw: Any) -> dict[str, Any] | None:
    """Solana Tracker — token detail (holders, top holders, markets)."""
    api_key = kw.get("api_key", "")
    if not mint or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://api.solanatracker.io/v1/token/{mint}",
                headers={"x-api-key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _solana_tracker_trending(**kw: Any) -> dict[str, Any] | None:
    """Solana Tracker — trending tokens."""
    api_key = kw.get("api_key", "")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.solanatracker.io/v1/trending",
                headers={"x-api-key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _gmgn_trending(**kw: Any) -> dict[str, Any] | None:
    """GMGN Agent API — trending tokens (GET /v1/market/rank).

    Uses the real GMGN Agent API via ``app.databus.gmgn_provider`` (the
    public web quotation API is Cloudflare-blocked). Requires GMGN_API_KEY
    from the environment; returns the same ``{"trending": [...], "source"}``
    shape as the other trending providers so the chain contract holds.
    """
    try:
        from app.databus.gmgn_provider import get_gmgn_provider

        chain = kw.get("chain", "solana")
        interval = kw.get("interval", "1h")
        limit = min(int(kw.get("limit", 20)), 100)
        data = await get_gmgn_provider().get_trending(chain=chain, interval=interval, limit=limit)
        if data:
            return {"trending": data, "source": "gmgn"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


# ── Messari (freemium, 200 req/min) ──


async def _messari_news(limit: int = 20, **kw: Any) -> dict[str, Any] | None:
    """Messari — crypto news with institutional quality."""
    api_key = kw.get("api_key", "")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://data.messari.io/api/v2/news",
                headers={"x-messari-api-key": api_key},
                params={"limit": limit},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


# ── CoinDesk (freemium, high-quality news) ──


async def _coindesk_news(limit: int = 20, **kw: Any) -> dict[str, Any] | None:
    """CoinDesk — professional crypto journalism."""
    api_key = kw.get("api_key", "")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.coindesk.com/v1/news",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"limit": limit},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


# ── Santiment (freemium, dev activity & social) ──


async def _santiment_dev_activity(project_slug: str = "bitcoin", **kw: Any) -> dict[str, Any] | None:
    """Santiment — GitHub developer activity."""
    api_key = kw.get("api_key", "")
    if not api_key:
        return None
    try:
        query = f"""
        {{
          getMetric(metric: "dev_activity") {{
            timeseriesData(
              slug: "{project_slug}"
              from: "2025-01-01T00:00:00Z"
              to: "2025-12-31T23:59:59Z"
              interval: "1w"
            ) {{
              datetime
              value
            }}
          }}
        }}
        """
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.santiment.net/graphql",
                json={"query": query},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


# ── VirusTotal (free, 500 req/day) ──


async def _virustotal_url_scan(url: str, **kw: Any) -> dict[str, Any] | None:
    """VirusTotal — URL security scan for phishing/malware."""
    api_key = kw.get("api_key", "")
    if not url or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://www.virustotal.com/api/v3/urls",
                data={"url": url},
                headers={"x-apikey": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


# ── Dune Analytics (free tier: 10K CU/mo + fallback) ──


async def _dune_early_buyers(token_address: str = "", chain: str = "ethereum", **kw: Any) -> dict[str, Any] | None:
    """Dune — first 5-minute buyers of a token (low-CU cached query).

    The Dune results endpoint requires a REAL query id. The previous
    hardcoded ``query/12345`` placeholder could never return data, so the
    query id now comes from the ``DUNE_QUERY_ID`` env var and the provider
    fails closed (returns None) when it is missing/invalid — no fabricated
    endpoints.
    """
    import os as _os

    api_key = kw.get("api_key", "")
    query_id = _os.getenv("DUNE_QUERY_ID", "").strip()
    if not token_address or not api_key or not query_id.isdigit():
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"https://api.dune.com/api/v1/query/{query_id}/results",
                headers={"x-dune-api-key": api_key},
                params={"token": token_address, "chain": chain},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None
