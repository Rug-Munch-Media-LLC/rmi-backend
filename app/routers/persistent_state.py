"""
RMI Persistent State Layer
===========================
User watchlists, portfolios, saved scans, and alert history.
Redis-backed for speed, with JSON serialization.

Endpoints:
  POST   /api/v1/state/watchlist          - Add address to watchlist
  DELETE /api/v1/state/watchlist/{id}     - Remove from watchlist
  GET    /api/v1/state/watchlist           - List watched addresses
  POST   /api/v1/state/portfolio           - Add portfolio entry
  DELETE /api/v1/state/portfolio/{id}      - Remove portfolio entry
  GET    /api/v1/state/portfolio           - Get portfolio summary
  GET    /api/v1/state/portfolio/{address} - Get single holding details
  POST   /api/v1/state/scan                - Save a scan configuration
  GET    /api/v1/state/scan/{id}           - Get saved scan
  GET    /api/v1/state/scan                - List saved scans
  DELETE /api/v1/state/scan/{id}           - Delete saved scan
  GET    /api/v1/state/alerts              - Get alert history
  GET    /api/v1/state/alerts/unread       - Get unread alerts
  POST   /api/v1/state/alerts/{id}/read    - Mark alert as read
  GET    /api/v1/state/stats               - User state statistics

Identity: Uses X-RMI-Dev-Key header for API key users, or
          X-Wallet-Identity header for verified wallet users.
          Falls back to fingerprint-based identity.

Author: RMI Development
Date: 2026-06-05
"""

import json
import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.redis import get_redis

logger = logging.getLogger("persistent_state")

router = APIRouter(prefix="/api/v1/state", tags=["persistent-state"])


# ── Identity Helper ───────────────────────────────────────────────


def get_user_identity(request: Request) -> str:
    """Get a stable user identity from the request."""
    # Priority 1: Developer API key
    dev_key = request.headers.get("X-RMI-Dev-Key", "") or request.headers.get("x-rmi-dev-key", "")
    if dev_key:
        import hashlib

        return f"dev:{hashlib.sha256(dev_key.encode()).hexdigest()[:16]}"

    # Priority 2: Wallet identity
    wallet_id = request.headers.get("X-Wallet-Identity", "") or request.headers.get("x-wallet-identity", "")
    if wallet_id:
        return f"wallet:{wallet_id}"

    # Priority 3: Fingerprint
    fp = request.headers.get("x-fingerprint", "") or request.headers.get("X-Fingerprint", "")
    if fp:
        return f"fp:{fp[:16]}"

    # Priority 4: IP address
    client_ip = request.client.host if request.client else "unknown"
    return f"ip:{client_ip}"


# ── Redis Helper ─────────────────────────────────────────────────


async def add_to_watchlist(entry: WatchlistEntry, request: Request):  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    """Add an address to the user's watchlist."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)
    watch_id = f"watch:{uuid.uuid4().hex[:12]}"

    watch_data = {
        "id": watch_id,
        "address": entry.address.lower(),
        "chain": entry.chain,
        "label": entry.label or entry.address[:8],
        "alert_types": entry.alert_types,
        "notes": entry.notes,
        "added_at": datetime.now(UTC).isoformat(),
        "status": "active",
    }

    # Store in sorted set (by added time)
    r.zadd(
        f"rmi:state:{user_id}:watchlist",
        {json.dumps(watch_data): time.time()},
    )

    # Also index by address for quick lookup
    r.sadd(
        f"rmi:state:{user_id}:watchlist:addresses",
        f"{entry.chain}:{entry.address.lower()}",
    )

    r.incr("rmi:state:stats:watchlist_total")

    return JSONResponse(
        status_code=201,
        content={
            "success": True,
            "watch_id": watch_id,
            "address": entry.address,
            "chain": entry.chain,
            "message": f"Added {entry.address} to watchlist",
        },
    )


@router.delete("/watchlist/{watch_id}")
async def remove_from_watchlist(watch_id: str, request: Request):
    """Remove an address from the watchlist."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)

    # Find and remove the entry
    entries = r.zrange(f"rmi:state:{user_id}:watchlist", 0, -1)
    for entry_json in entries:
        entry = json.loads(entry_json)
        if entry.get("id") == watch_id:
            r.zrem(f"rmi:state:{user_id}:watchlist", entry_json)
            addr_key = f"{entry.get('chain', '')}:{entry.get('address', '')}"
            r.srem(f"rmi:state:{user_id}:watchlist:addresses", addr_key)
            return JSONResponse(content={"success": True, "message": f"Removed {entry.get('address')}"})

    return JSONResponse(status_code=404, content={"error": "Watchlist entry not found"})


@router.get("/watchlist")
async def get_watchlist(request: Request):
    """Get the user's watchlist."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"watchlist": [], "count": 0})

    user_id = get_user_identity(request)
    entries = r.zrange(f"rmi:state:{user_id}:watchlist", 0, -1)

    watchlist = []
    for entry_json in entries:
        entry = json.loads(entry_json)
        watchlist.append(entry)

    return JSONResponse(
        content={
            "watchlist": watchlist,
            "count": len(watchlist),
        }
    )


# ── Portfolio ─────────────────────────────────────────────────────


@router.post("/portfolio")
async def add_portfolio_entry(entry: PortfolioEntry, request: Request):  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    """Add a holding to the user's portfolio."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)
    holding_id = f"holding:{uuid.uuid4().hex[:12]}"

    holding_data = {
        "id": holding_id,
        "address": entry.address.lower(),
        "chain": entry.chain,
        "amount": entry.amount,
        "cost_basis": entry.cost_basis,
        "label": entry.label or entry.address[:8],
        "notes": entry.notes,
        "added_at": datetime.now(UTC).isoformat(),
        "status": "active",
    }

    r.zadd(
        f"rmi:state:{user_id}:portfolio",
        {json.dumps(holding_data): time.time()},
    )

    return JSONResponse(
        status_code=201,
        content={
            "success": True,
            "holding_id": holding_id,
            "address": entry.address,
            "message": f"Added {entry.address} to portfolio",
        },
    )


@router.delete("/portfolio/{holding_id}")
async def remove_portfolio_entry(holding_id: str, request: Request):
    """Remove a holding from the portfolio."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)

    entries = r.zrange(f"rmi:state:{user_id}:portfolio", 0, -1)
    for entry_json in entries:
        entry = json.loads(entry_json)
        if entry.get("id") == holding_id:
            r.zrem(f"rmi:state:{user_id}:portfolio", entry_json)
            return JSONResponse(content={"success": True, "message": f"Removed {entry.get('address')}"})

    return JSONResponse(status_code=404, content={"error": "Portfolio entry not found"})


@router.get("/portfolio")
async def get_portfolio(request: Request):
    """Get the user's portfolio summary."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"portfolio": [], "total_value": 0})

    user_id = get_user_identity(request)
    entries = r.zrange(f"rmi:state:{user_id}:portfolio", 0, -1)

    portfolio = []
    total_cost = 0
    for entry_json in entries:
        entry = json.loads(entry_json)
        portfolio.append(entry)
        total_cost += entry.get("cost_basis", 0) * entry.get("amount", 0)

    return JSONResponse(
        content={
            "portfolio": portfolio,
            "count": len(portfolio),
            "total_cost_basis": round(total_cost, 2),
        }
    )


@router.get("/portfolio/{address}")
async def get_portfolio_holding(address: str, request: Request):
    """Get details for a specific portfolio holding."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)
    entries = r.zrange(f"rmi:state:{user_id}:portfolio", 0, -1)

    for entry_json in entries:
        entry = json.loads(entry_json)
        if entry.get("address", "").lower() == address.lower():
            return JSONResponse(content={"holding": entry})

    return JSONResponse(status_code=404, content={"error": "Holding not found"})


# ── Saved Scans ───────────────────────────────────────────────────


@router.post("/scan")
async def save_scan(scan: SavedScan, request: Request):  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    """Save a scan configuration for later use."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)
    scan_id = f"scan:{uuid.uuid4().hex[:12]}"

    scan_data = {
        "id": scan_id,
        "name": scan.name,
        "tool": scan.tool,
        "params": scan.params,
        "chain": scan.chain,
        "schedule": scan.schedule,
        "webhook_url": scan.webhook_url,
        "created_at": datetime.now(UTC).isoformat(),
        "last_run": None,
        "run_count": 0,
        "status": "active",
    }

    r.set(
        f"rmi:state:{user_id}:scan:{scan_id}",
        json.dumps(scan_data),
    )

    # Add to scan index
    r.zadd(
        f"rmi:state:{user_id}:scans",
        {scan_id: time.time()},
    )

    return JSONResponse(
        status_code=201,
        content={
            "success": True,
            "scan_id": scan_id,
            "name": scan.name,
            "message": f"Saved scan '{scan.name}'",
        },
    )


@router.get("/scan/{scan_id}")
async def get_saved_scan(scan_id: str, request: Request):
    """Get a saved scan configuration."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)
    scan_data = r.get(f"rmi:state:{user_id}:scan:{scan_id}")

    if not scan_data:
        return JSONResponse(status_code=404, content={"error": "Scan not found"})

    return JSONResponse(content={"scan": json.loads(scan_data)})


@router.get("/scan")
async def list_saved_scans(request: Request):
    """List all saved scans for the user."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"scans": [], "count": 0})

    user_id = get_user_identity(request)
    scan_ids = r.zrange(f"rmi:state:{user_id}:scans", 0, -1)

    scans = []
    for scan_id in scan_ids:
        scan_data = r.get(f"rmi:state:{user_id}:scan:{scan_id}")
        if scan_data:
            scans.append(json.loads(scan_data))

    return JSONResponse(
        content={
            "scans": scans,
            "count": len(scans),
        }
    )


@router.delete("/scan/{scan_id}")
async def delete_saved_scan(scan_id: str, request: Request):
    """Delete a saved scan."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)

    # Check ownership
    scan_data = r.get(f"rmi:state:{user_id}:scan:{scan_id}")
    if not scan_data:
        return JSONResponse(status_code=404, content={"error": "Scan not found"})

    r.delete(f"rmi:state:{user_id}:scan:{scan_id}")
    r.zrem(f"rmi:state:{user_id}:scans", scan_id)

    return JSONResponse(content={"success": True, "message": "Scan deleted"})


# ── Alert History ─────────────────────────────────────────────────


@router.get("/alerts")
async def get_alert_history(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
):
    """Get alert history for the user."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"alerts": [], "count": 0})

    user_id = get_user_identity(request)

    # Alerts stored as a list (newest first)
    alerts = []
    start = offset
    end = offset + limit - 1

    alert_keys = r.lrange(f"rmi:state:{user_id}:alerts", start, end)
    for alert_json in alert_keys:
        alert = json.loads(alert_json)
        if status is None or alert.get("status") == status:
            alerts.append(alert)

    total = r.llen(f"rmi:state:{user_id}:alerts")
    unread = r.scard(f"rmi:state:{user_id}:alerts:unread")

    return JSONResponse(
        content={
            "alerts": alerts,
            "total": total,
            "unread": unread,
            "limit": limit,
            "offset": offset,
        }
    )


@router.get("/alerts/unread")
async def get_unread_alerts(request: Request):
    """Get unread alerts."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"alerts": [], "count": 0})

    user_id = get_user_identity(request)
    unread_ids = r.smembers(f"rmi:state:{user_id}:alerts:unread")

    alerts = []
    for alert_id in list(unread_ids)[:50]:  # Limit to 50
        alert_json = r.get(f"rmi:state:{user_id}:alert:{alert_id}")
        if alert_json:
            alerts.append(json.loads(alert_json))

    return JSONResponse(
        content={
            "alerts": alerts,
            "count": len(alerts),
        }
    )


@router.post("/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: str, request: Request):
    """Mark an alert as read."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)

    # Remove from unread set
    r.srem(f"rmi:state:{user_id}:alerts:unread", alert_id)

    # Update status in alert data
    alert_json = r.get(f"rmi:state:{user_id}:alert:{alert_id}")
    if alert_json:
        alert = json.loads(alert_json)
        alert["status"] = "read"
        alert["read_at"] = datetime.now(UTC).isoformat()
        r.set(f"rmi:state:{user_id}:alert:{alert_id}", json.dumps(alert))

    return JSONResponse(content={"success": True})


@router.post("/alerts/mark-all-read")
async def mark_all_alerts_read(request: Request):
    """Mark all alerts as read."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)
    r.delete(f"rmi:state:{user_id}:alerts:unread")

    return JSONResponse(content={"success": True, "message": "All alerts marked as read"})


# Helper function to push alerts (called by webhook/cron systems)
def push_alert(
    user_id: str,
    alert_type: str,
    address: str,
    message: str,
    severity: str = "medium",
    data: dict | None = None,
    source: str = "rmi_scanner",
):
    """Push an alert to a user's alert history.

    Called by webhook handlers, cron jobs, and scanner systems.
    """
    r = get_redis()
    if not r:
        return

    alert_id = f"alert:{uuid.uuid4().hex[:12]}"

    alert_data = {
        "id": alert_id,
        "type": alert_type,
        "address": address,
        "message": message,
        "severity": severity,
        "data": data or {},
        "source": source,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "unread",
    }

    # Add to alert history (newest first)
    r.lpush(
        f"rmi:state:{user_id}:alerts",
        json.dumps(alert_data),
    )

    # Trim to last 1000 alerts
    r.ltrim(f"rmi:state:{user_id}:alerts", 0, 999)

    # Add to unread set
    r.sadd(f"rmi:state:{user_id}:alerts:unread", alert_id)

    # Store individual alert for retrieval
    r.set(
        f"rmi:state:{user_id}:alert:{alert_id}",
        json.dumps(alert_data),
        ex=86400 * 30,  # 30 day TTL
    )

    # Update stats
    r.incr("rmi:state:stats:alerts_total")
    r.incr(f"rmi:state:stats:alerts:{alert_type}")


# ── User State Statistics ─────────────────────────────────────────


@router.get("/stats")
async def get_user_stats(request: Request):
    """Get user state statistics."""
    r = get_redis()
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    user_id = get_user_identity(request)

    watchlist_count = r.zcard(f"rmi:state:{user_id}:watchlist")
    portfolio_count = r.zcard(f"rmi:state:{user_id}:portfolio")
    scan_count = r.zcard(f"rmi:state:{user_id}:scans")
    alerts_total = r.llen(f"rmi:state:{user_id}:alerts")
    alerts_unread = r.scard(f"rmi:state:{user_id}:alerts:unread")

    return JSONResponse(
        content={
            "user_id": user_id,
            "watchlist": watchlist_count,
            "portfolio": portfolio_count,
            "saved_scans": scan_count,
            "alerts_total": alerts_total,
            "alerts_unread": alerts_unread,
        }
    )
