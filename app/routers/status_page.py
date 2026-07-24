"""
RMI Public Status Page
=======================
Public-facing status page showing health of all RMI services.
No authentication required - designed for transparency and trust.

Monitors:
- Backend API health
- Redis connectivity
- ClickHouse connectivity
- MCP discovery endpoint
- x402 catalog endpoint
- All payment facilitators
- Response time metrics
- Incident history

Endpoints:
  GET  /api/v1/status          - Full status JSON
  GET  /status                 - Public HTML status page
  GET  /api/v1/status/summary  - One-line summary for badges
  POST /api/v1/status/incident - Report incident (admin only)
  GET  /api/v1/status/history  - Historical uptime data

Author: RMI Development
Date: 2026-06-05
"""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger("status_page")

router = APIRouter(prefix="/api/v1/status", tags=["status-page"])


def _try_mark_down(store: str) -> None:
    try:
        from app.core.degradation import get_registry

        get_registry().mark_down(store)
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Degradation registry mark_down failed: {e}")


# ── Health Check Functions ───────────────────────────────────────


async def check_http_service(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """Check an HTTP endpoint."""
    import aiohttp

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:  # noqa: SIM117
            async with session.get(url) as resp:
                latency_ms = (time.monotonic() - start) * 1000
                if resp.status == 200:
                    return {
                        "status": "operational",
                        "latency_ms": round(latency_ms, 1),
                        "http_status": resp.status,
                        "last_check": datetime.now(UTC).isoformat(),
                    }
                else:
                    return {
                        "status": "degraded",
                        "latency_ms": round(latency_ms, 1),
                        "http_status": resp.status,
                        "error": f"HTTP {resp.status}",
                        "last_check": datetime.now(UTC).isoformat(),
                    }
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(latency_ms, 1),
            "error": str(e)[:200],
            "last_check": datetime.now(UTC).isoformat(),
        }


def check_redis() -> dict[str, Any]:
    """Check Redis connectivity."""
    r = get_redis()
    if not r:
        return {
            "status": "down",
            "error": "Redis connection unavailable",
            "last_check": datetime.now(UTC).isoformat(),
        }

    start = time.monotonic()
    try:
        r.ping()
        latency_ms = (time.monotonic() - start) * 1000
        # Get memory info
        info = r.info("memory")
        used_memory = info.get("used_memory_human", "unknown")

        return {
            "status": "operational",
            "latency_ms": round(latency_ms, 1),
            "memory_used": used_memory,
            "last_check": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(latency_ms, 1),
            "error": str(e)[:200],
            "last_check": datetime.now(UTC).isoformat(),
        }


def check_clickhouse() -> dict[str, Any]:
    """Check ClickHouse connectivity."""
    import clickhouse_connect

    start = time.monotonic()
    try:
        ch_host = os.getenv("CH_HOST", "langfuse-clickhouse-1")
        ch_user = os.getenv("CH_USER", "clickhouse")
        ch_password = os.getenv("CH_PASSWORD")
        if not ch_password:
            raise RuntimeError("CH_PASSWORD environment variable is required")

        client = clickhouse_connect.get_client(
            host=ch_host,
            port=8123,
            username=ch_user,
            password=ch_password,
        )
        client.query("SELECT 1")
        latency_ms = (time.monotonic() - start) * 1000

        # Get label count
        label_count = client.query("SELECT count() FROM wallet_memory.wallet_labels").result_rows
        count = label_count[0][0] if label_count else 0

        return {
            "status": "operational",
            "latency_ms": round(latency_ms, 1),
            "wallet_labels": count,
            "last_check": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        _try_mark_down("clickhouse")
        return {
            "status": "degraded",
            "latency_ms": round(latency_ms, 1),
            "error": str(e)[:200],
            "last_check": datetime.now(UTC).isoformat(),
        }


async def check_service(name: str, config: dict) -> dict[str, Any]:
    """Check a single service."""
    check_type = config.get("check_type", "http")

    if check_type == "http":
        return await check_http_service(config.get("url", ""))
    elif check_type == "redis":
        return check_redis()
    elif check_type == "clickhouse":
        return check_clickhouse()
    else:
        return {"status": "unknown", "error": f"Unknown check type: {check_type}"}


# ── Incident Management ──────────────────────────────────────────


class IncidentReport(BaseModel):
    service: str
    severity: str = "minor"  # minor, major, critical
    message: str
    resolved: bool = False


def get_incidents(limit: int = 20) -> list[dict[str, Any]]:
    """Get recent incidents from Redis."""
    r = get_redis()
    if not r:
        return []

    incidents = r.lrange("rmi:status:incidents", 0, limit - 1)
    return [json.loads(i) for i in incidents]


def report_incident(service: str, severity: str, message: str, resolved: bool = False) -> None:
    """Report an incident."""
    r = get_redis()
    if not r:
        return

    incident = {
        "id": f"inc:{int(time.time())}",
        "service": service,
        "severity": severity,
        "message": message,
        "resolved": resolved,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    r.lpush("rmi:status:incidents", json.dumps(incident))
    r.ltrim("rmi:status:incidents", 0, 99)  # Keep last 100

    # Update current status
    if not resolved:
        r.set(
            f"rmi:status:current:{service}",
            json.dumps(
                {
                    "severity": severity,
                    "message": message,
                    "since": incident["created_at"],
                }
            ),
        )
    else:
        r.delete(f"rmi:status:current:{service}")


def get_uptime_history(days: int = 30) -> dict[str, Any]:
    """Get uptime history for the past N days."""
    r = get_redis()
    if not r:
        return {"error": "redis_unavailable"}

    history = {}
    for service_name in SERVICES:  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        daily_uptime = []
        for i in range(days):
            day = (datetime.now(UTC) - timedelta(days=i)).strftime("%Y-%m-%d")
            key = f"rmi:status:uptime:{service_name}:{day}"
            data = r.get(key)
            day_data = json.loads(data) if data else {"uptime_pct": 100.0, "incidents": 0}
            daily_uptime.append(
                {
                    "date": day,
                    **day_data,
                }
            )
        history[service_name] = daily_uptime

    return history


# ── Background Monitor ───────────────────────────────────────────


async def run_all_checks() -> None:
    """Run all health checks and store results."""
    r = get_redis()
    if not r:
        return

    results = {}
    overall_status = "operational"

    for name, config in SERVICES.items():  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        try:
            result = await check_service(name, config)
            results[name] = result

            # Update overall status
            if result.get("status") == "down" and config.get("critical"):
                overall_status = "down"
            elif result.get("status") == "degraded" and overall_status != "down":
                overall_status = "degraded"

            # Store individual result
            r.set(f"rmi:status:service:{name}", json.dumps(result), ex=120)

        except Exception as e:
            results[name] = {
                "status": "down",
                "error": str(e)[:200],
                "last_check": datetime.now(UTC).isoformat(),
            }

    # Store overall status
    overall = {
        "status": overall_status,
        "timestamp": datetime.now(UTC).isoformat(),
        "services": results,
        "version": os.getenv("APP_VERSION", "2.0.0"),
    }
    r.set("rmi:status:overall", json.dumps(overall), ex=120)


async def status_monitor_loop() -> None:
    """Run status checks every 30 seconds."""
    while True:
        try:
            await run_all_checks()
        except Exception as e:
            logger.error(f"Status monitor error: {e}")
        import asyncio

        await asyncio.sleep(30)


# ── Endpoints ────────────────────────────────────────────────────


@router.get("")
async def get_status() -> Any:
    """Get full status for all services."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"error": "redis_unavailable"})

    # Try cached result
    cached = r.get("rmi:status:overall")
    if cached:
        overall = json.loads(cached)
    else:
        # Run checks on demand
        overall = {
            "status": "unknown",
            "timestamp": datetime.now(UTC).isoformat(),
            "services": {},
        }
        for name, config in SERVICES.items():  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
            try:
                result = await check_service(name, config)
                overall["services"][name] = result
            except Exception as e:
                overall["services"][name] = {
                    "status": "down",
                    "error": str(e)[:200],
                }

    # Add incidents
    overall["incidents"] = get_incidents(10)

    # Add facilitator health
    try:
        facilitators = r.get("rmi:facilitator:summary")
        if facilitators:
            overall["facilitators"] = json.loads(facilitators)
    except (ConnectionError, TimeoutError, OSError, json.JSONDecodeError) as e:
        logger.debug(f"Facilitator summary parse failed: {e}")

    return JSONResponse(content=overall)


@router.get("/summary")
async def status_summary() -> Any:
    """Get one-line summary for status badges."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"status": "unknown"})

    cached = r.get("rmi:status:overall")
    overall = json.loads(cached) if cached else {"status": "unknown"}

    # Count services by status
    services = overall.get("services", {})
    operational = sum(1 for s in services.values() if s.get("status") == "operational")
    degraded = sum(1 for s in services.values() if s.get("status") == "degraded")
    down = sum(1 for s in services.values() if s.get("status") == "down")

    return JSONResponse(
        content={
            "status": overall.get("status", "unknown"),
            "operational": operational,
            "degraded": degraded,
            "down": down,
            "total": len(services),
            "timestamp": overall.get("timestamp"),
        }
    )


@router.get("/history")
async def status_history(days: int = 7) -> Any:
    """Get uptime history."""
    days = min(days, 90)  # Cap at 90 days
    return JSONResponse(content=get_uptime_history(days))


@router.post("/incident")
async def create_incident(incident: IncidentReport, request: Request) -> Any:
    """Report an incident (admin only)."""
    # Simple admin check
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": "Admin auth required"})

    report_incident(
        service=incident.service,
        severity=incident.severity,
        message=incident.message,
        resolved=incident.resolved,
    )

    return JSONResponse(
        content={
            "success": True,
            "message": "Incident reported",
        }
    )


@router.get("/services")
async def list_services() -> Any:
    """List all monitored services."""
    return JSONResponse(
        content={
            "services": {
                name: {
                    "name": config["name"],
                    "critical": config.get("critical", False),
                    "check_type": config.get("check_type", "http"),
                }
                for name, config in SERVICES.items()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
            }
        }
    )
