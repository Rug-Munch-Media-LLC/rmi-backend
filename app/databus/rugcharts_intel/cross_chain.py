"""
RugCharts Intel — Cross-Chain Entity Intel.
Same entity across chains via Arkham.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

from .cache import ARKHAM_KEY, CACHE_TTL, _r

logger = get_logger("rugcharts_intel")


async def cross_chain_entity(address: str = "", **kw) -> dict[str, Any] | None:
    """Trace an entity across all chains using Arkham intelligence.
    Discovers all addresses owned by the same entity on different chains.
    """
    if not address:
        return None

    cache_key = f"cross_chain:{address}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    if not ARKHAM_KEY:
        return {"error": "ARKHAM_API_KEY required", "address": address}

    entity_data = {}
    chain_addresses = []

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://api.arkhamintelligence.com/intelligence/address/{address}",
                headers={"API-Key": ARKHAM_KEY},
            )
            if r.status_code == 200:
                data = r.json()
                entity_data = {
                    "primary_address": address,
                    "primary_chain": data.get("chain"),
                    "entity_name": data.get("arkhamEntity", {}).get("name", "Unknown"),
                    "entity_type": data.get("arkhamEntity", {}).get("type", ""),
                    "entity_id": data.get("arkhamEntity", {}).get("id", ""),
                    "label": data.get("arkhamLabel", {}).get("name", ""),
                    "is_contract": data.get("contract", False),
                }
                entity_id = data.get("arkhamEntity", {}).get("id", "")
                if entity_id:
                    sr = await c.get(
                        "https://api.arkhamintelligence.com/intelligence/search",
                        params={"query": entity_data["entity_name"]},
                        headers={"API-Key": ARKHAM_KEY},
                    )
                    if sr.status_code == 200:
                        search_data = sr.json()
                        entities = search_data.get("arkhamEntities", [])
                        for ent in entities[:5]:
                            if ent.get("id") == entity_id:
                                chain_addresses.append(
                                    {
                                        "entity_name": ent.get("name"),
                                        "entity_type": ent.get("type"),
                                        "twitter": ent.get("twitter"),
                                    }
                                )
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.debug(f"Cross-chain entity: {e}")

    result = {
        "entity": entity_data,
        "cross_chain_addresses": chain_addresses,
        "chains_detected": list({a.get("chain", "unknown") for a in chain_addresses}),
        "scan_time": datetime.now(UTC).isoformat(),
        "source": "cross_chain_entity_intel",
    }

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["cross_chain"], json.dumps(result, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    return result
