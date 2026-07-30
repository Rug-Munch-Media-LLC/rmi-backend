"""DataBus Router sub-module. Admin endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.logging import get_logger
from app.databus.core import databus

logger = get_logger("databus.router.admin")
router = APIRouter(tags=["databus"])


@router.post("/invalidate")
async def databus_invalidate(request: Request) -> dict[str, str]:
    """Clear cache. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    data_type = body.get("data_type")

    if data_type:
        await databus.invalidate(data_type, **{k: v for k, v in body.items() if k != "data_type"})
        return {"status": "invalidated", "data_type": data_type}
    else:
        await databus.invalidate_all()
        return {"status": "all_cache_cleared"}


@router.get("/vault/status")
async def databus_vault_status(request: Request) -> Any:
    """Key pool status. NEVER exposes key values. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    if not databus.vault:
        await databus.initialize()
    return databus.vault.status()


@router.get("/vault/capacity")
async def databus_vault_capacity(request: Request) -> Any:
    """Detailed capacity report with free tier recommendations. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    if not databus.vault:
        await databus.initialize()
    return databus.vault.capacity_report()


@router.post("/vault/reload")
async def databus_vault_reload(request: Request) -> dict[str, str]:
    """Reload keys from vault without restart. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    await databus.vault.reload()
    return {"status": "reloaded", "providers": len(databus.vault.pools)}


@router.post("/vault/add-key")
async def databus_vault_add_key(request: Request) -> dict[str, str]:
    """Hot-add an API key to a provider pool. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    body = await request.json()
    provider = body.get("provider")
    key_name = body.get("key_name")
    key_value = body.get("key_value")

    if not all([provider, key_name, key_value]):
        raise HTTPException(400, "provider, key_name, key_value required")

    await databus.vault.add_key(provider, key_name, key_value)
    return {"status": "added", "provider": provider, "key_name": key_name}


@router.post("/vault/reset-monthly")
async def databus_vault_reset_monthly(request: Request) -> dict[str, str]:
    """Reset monthly call counters. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    databus.vault.reset_monthly_counters()
    return {"status": "monthly_counters_reset"}


@router.post("/warm/start")
async def databus_warm_start(request: Request) -> dict[str, str]:
    """Start cache warm background task. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    interval = body.get("interval_seconds", 30)
    await databus.start_cache_warm(interval_seconds=interval)
    return {"status": "started", "interval_seconds": interval}


@router.post("/warm/stop")
async def databus_warm_stop(request: Request) -> dict[str, str]:
    """Stop cache warm background task. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")
    await databus.stop_cache_warm()
    return {"status": "stopped"}
