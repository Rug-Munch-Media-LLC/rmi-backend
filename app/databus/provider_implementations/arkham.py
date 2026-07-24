"""Arkham Intelligence providers — Free trial month, premium entity data."""

from __future__ import annotations
import httpx

from sdks.python.x402_frameworks.autogen_adapter import logger

_ARKHAM_BASE = "https://api.arkhamintelligence.com"

async def _arkham_entity(address: str = "", **kw) -> dict | None:
    """Arkham - entity intelligence (tx counts, top counterparties, tags)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{_ARKHAM_BASE}/intelligence/address/{address}", headers=headers)
            if r.status_code == 200:
                return {"entity": r.json(), "address": address, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _arkham_counterparties(address: str = "", **kw) -> dict | None:
    """Arkham - top counterparties ranked by transaction volume."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        limit = kw.get("limit", 25)
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_ARKHAM_BASE}/counterparties/address/{address}",
                params={"limit": limit},
                headers=headers,
            )
            if r.status_code == 200:
                return {"counterparties": r.json(), "address": address, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _arkham_intel_search(query: str = "", **kw) -> dict | None:
    """Arkham - search entities by address prefix (up to 20 matches)."""
    api_key = kw.get("api_key", "")
    if not query or not api_key:
        return None
    try:
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_ARKHAM_BASE}/intelligence/search", params={"query": query}, headers=headers
            )
            if r.status_code == 200:
                return {"results": r.json(), "query": query, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _arkham_portfolio(address: str = "", **kw) -> dict | None:
    """Arkham - portfolio via entity intelligence (includes balance info)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{_ARKHAM_BASE}/intelligence/address/{address}", headers=headers)
            if r.status_code == 200:
                return {"portfolio": r.json(), "address": address, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _arkham_transfers(address: str = "", **kw) -> dict | None:
    """Arkham - transfer history via counterparties endpoint."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        limit = kw.get("limit", 100)
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{_ARKHAM_BASE}/counterparties/address/{address}",
                params={"limit": limit},
                headers=headers,
            )
            if r.status_code == 200:
                return {"transfers": r.json(), "address": address, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _arkham_labels(address: str = "", **kw) -> dict | None:
    """Arkham - labels extracted from entity intelligence (arkhamLabel field)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{_ARKHAM_BASE}/intelligence/address/{address}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                label = data.get("arkhamLabel", {})
                entity = data.get("arkhamEntity", {})
                labels = []
                if label and label.get("name"):
                    labels.append({"name": label["name"], "id": label.get("id"), "type": "arkham"})
                if entity and entity.get("name"):
                    labels.append(
                        {"name": entity["name"], "id": entity.get("id"), "type": "entity"}
                    )
                return {
                    "labels": labels,
                    "address": address,
                    "chain": data.get("chain"),
                    "source": "arkham",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
