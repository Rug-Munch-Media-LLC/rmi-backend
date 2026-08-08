"""DataBus Router sub-module. Batch_Cache endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.logging import get_logger
from app.databus.core import databus
from app.databus.router._shared import verify_admin as _verify_admin

logger = get_logger("databus.router.batch_cache")
router = APIRouter(tags=["databus"])


@router.post("/batch")
async def databus_batch(request: Request) -> dict[str, Any]:
    """#7 Batch DataBus - fetch multiple data types in one call.

    Body: {
        "requests": [
            {"data_type": "token_price", "mint": "So111..."},
            {"data_type": "wallet_labels", "address": "0x..."},
            ...
        ],
        "admin_key": "",       # optional, applies to all
        "consumer_type": "",   # optional, applies to all
    }
    Returns: array of results in same order as requests.
    """
    body = await request.json()
    requests_list = body.get("requests", [])
    if not requests_list:
        raise HTTPException(400, "requests array is required")
    if len(requests_list) > 20:
        raise HTTPException(400, "Maximum 20 requests per batch")

    admin_key = body.get("admin_key", "")
    consumer_type = body.get("consumer_type", "")

    # Inject shared params into each request
    for req in requests_list:
        if admin_key and "admin_key" not in req:
            req["admin_key"] = admin_key
        if consumer_type and "consumer_type" not in req:
            req["consumer_type"] = consumer_type
        req["request"] = request

    results = await databus.fetch_batch(requests_list)
    return {"results": results, "count": len(results)}


@router.get("/providers/health")
async def databus_providers_health(request: Request) -> dict[str, Any]:
    """#5 Provider Health Dashboard - per-provider success/failure/latency/circuit status."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")
    if not databus._initialized:
        await databus.initialize()
    return {
        "provider_health": databus._provider_health.all_health(),
        "deduplication": databus._dedup.stats(),
    }


@router.get("/cache/stats")
async def databus_cache_stats(request: Request) -> dict[str, Any]:
    """#10 Per-type cache stats with TTL tuning suggestions."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")
    return {
        "per_type": databus.cache.type_stats(),
        "tuning_alerts": [
            {"data_type": dt, **stats} for dt, stats in databus.cache.type_stats().items() if stats.get("suggestion")
        ],
    }


@router.post("/schema/validate")
async def databus_schema_validate(request: Request) -> dict[str, Any]:
    """#15 Validate a DataBus response against expected schema.

    Body: {"data_type": "token_price", "data": {"price_usd": 1.23, ...}}
    Returns: {valid: bool, missing_fields: [...]}
    """
    body = await request.json()
    data_type = body.get("data_type", "")
    data = body.get("data", {})
    from app.databus.response_schema import schema_validator

    is_valid, missing = schema_validator.validate(data_type, data)
    return {"data_type": data_type, "valid": is_valid, "missing_fields": missing}
