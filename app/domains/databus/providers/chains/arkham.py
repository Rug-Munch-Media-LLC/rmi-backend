"""
Arkham Intelligence providers — free trial, premium entity data.

Covers: entity resolution, labels, portfolio, transfers, counterparties,
and intel search.
"""


import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.arkham")


async def _arkham_entity(address: str = "", **kw) -> dict | None:
    """Arkham — entity resolution for an address."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://api.arkhamintelligence.com/entity/{address}",
                headers={"API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_counterparties(address: str = "", **kw) -> dict | None:
    """Arkham — counterparty network mapping."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://api.arkhamintelligence.com/entity/{address}/counterparties",
                headers={"API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_intel_search(query: str = "", **kw) -> dict | None:
    """Arkham — entity intelligence search."""
    api_key = kw.get("api_key", "")
    if not query or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.arkhamintelligence.com/search",
                headers={"API-Key": api_key},
                params={"q": query},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_portfolio(address: str = "", **kw) -> dict | None:
    """Arkham — portfolio holdings with USD values."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://api.arkhamintelligence.com/portfolio/{address}",
                headers={"API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_transfers(address: str = "", **kw) -> dict | None:
    """Arkham — transfer history with counterparty mapping."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://api.arkhamintelligence.com/transfers/{address}",
                headers={"API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_labels(address: str = "", **kw) -> dict | None:
    """Arkham — entity labels with confidence scores."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.arkhamintelligence.com/labels/{address}",
                headers={"API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
