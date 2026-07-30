"""
Freemium API providers — free tier with API key, limited monthly quotas.

Includes: Birdeye, Solana Tracker, Messari, CoinDesk, Santiment,
VirusTotal, Dune Analytics.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.freemium_apis")

# ── Birdeye (freemium, free tier available) ──


async def _birdeye_overview(address: str = "", **kw) -> dict | None:
    """Birdeye — token overview, liquidity, holder stats (Solana/EVM)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://public-api.birdeye.so/public/token_overview?address={address}",
                headers={"x-api-key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _birdeye_price(address: str = "", **kw) -> dict | None:
    """Birdeye — real-time token price."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://public-api.birdeye.so/public/price?address={address}",
                headers={"x-api-key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── Solana Tracker (freemium, 5K/mo free quota) ──


async def _solana_tracker_price(mint: str = "", **kw) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _solana_tracker_token(mint: str = "", **kw) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _solana_tracker_trending(**kw) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── Messari (freemium, 200 req/min) ──


async def _messari_news(limit: int = 20, **kw) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── CoinDesk (freemium, high-quality news) ──


async def _coindesk_news(limit: int = 20, **kw) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── Santiment (freemium, dev activity & social) ──


async def _santiment_dev_activity(project_slug: str = "bitcoin", **kw) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── VirusTotal (free, 500 req/day) ──


async def _virustotal_url_scan(url: str, **kw) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── Dune Analytics (free tier: 10K CU/mo + fallback) ──


async def _dune_early_buyers(token_address: str = "", chain: str = "ethereum", **kw) -> dict | None:
    """Dune — first 5-minute buyers of a token (low-CU cached query)."""
    api_key = kw.get("api_key", "")
    if not token_address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                "https://api.dune.com/api/v1/query/12345/results",
                headers={"x-dune-api-key": api_key},
                params={"token": token_address, "chain": chain},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
