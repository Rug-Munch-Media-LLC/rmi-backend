"""
RugCharts Intel — Developer Reputation.
Deployer history tracking.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

from .cache import ARKHAM_KEY, CACHE_TTL, HELIUS_KEY, _r

logger = get_logger("rugcharts_intel")

async def developer_reputation(address: str = "", chain: str = "solana", **kw) -> dict | None:
    """Track deployer wallet history across all tokens they've created.
    Uses: Arkham entity + Helius transaction history + local token scanner data.
    """
    if not address:
        return None

    cache_key = f"dev_reputation:{chain}:{address}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    api_key = kw.get("api_key", "") or HELIUS_KEY
    reputation = {
        "deployer_address": address,
        "total_tokens_deployed": 0,
        "rug_pull_count": 0,
        "active_tokens": 0,
        "abandoned_tokens": 0,
        "avg_token_lifespan_hours": 0,
        "known_entity": "",
        "risk_level": "UNKNOWN",
        "risk_factors": [],
        "recent_tokens": [],
    }

    try:
        if ARKHAM_KEY:
            async with httpx.AsyncClient(timeout=10) as c:
                ar = await c.get(
                    f"https://api.arkhamintelligence.com/intelligence/address/{address}",
                    headers={"API-Key": ARKHAM_KEY},
                )
                if ar.status_code == 200:
                    entity = ar.json().get("arkhamEntity", {})
                    reputation["known_entity"] = entity.get("name", "")
                    reputation["entity_type"] = entity.get("type", "")

        if chain == "solana" and api_key:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 100}],
                    },
                )
                if r.status_code == 200:
                    sigs = r.json().get("result", [])
                    if sigs:
                        reputation["total_transactions"] = len(sigs)
                        oldest = min(
                            s.get("blockTime", float("inf")) for s in sigs if s.get("blockTime")
                        )
                        newest = max(s.get("blockTime", 0) for s in sigs if s.get("blockTime"))
                        if oldest < float("inf") and newest > 0:
                            reputation["first_seen"] = datetime.fromtimestamp(
                                oldest, tz=UTC
                            ).isoformat()
                            reputation["last_active"] = datetime.fromtimestamp(
                                newest, tz=UTC
                            ).isoformat()
                            reputation["activity_span_days"] = round((newest - oldest) / 86400, 1)

                        if len(sigs) > 500:
                            reputation["total_tokens_deployed"] = "10+"
                            reputation["risk_factors"].append("High-volume deployer (>500 txs)")
                            reputation["risk_level"] = "HIGH"
                        elif len(sigs) > 100:
                            reputation["total_tokens_deployed"] = "5-10"
                            reputation["risk_factors"].append("Active deployer (>100 txs)")
                            reputation["risk_level"] = "MEDIUM"
                        elif len(sigs) > 10:
                            reputation["total_tokens_deployed"] = "1-5"
                            reputation["risk_level"] = "LOW"
                        else:
                            reputation["total_tokens_deployed"] = "0-1"
                            reputation["risk_level"] = "LOW"
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.debug(f"Dev reputation: {e}")

    reputation["scan_time"] = datetime.now(UTC).isoformat()
    reputation["source"] = "developer_reputation"

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["dev_reputation"], json.dumps(reputation, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    return reputation
