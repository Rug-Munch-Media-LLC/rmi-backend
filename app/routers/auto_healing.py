"""Auto-Healing Infrastructure — self-diagnosing, self-repairing platform."""

import os
from datetime import UTC, datetime

import httpx
import psutil
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/health", tags=["auto-healing"])

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
CHECKS = [
    ("redis", "http://localhost:6379", "Redis"),
    ("postgres", "http://localhost:5432", "Postgres"),
    ("ollama", "http://localhost:11434/api/tags", "Ollama"),
    ("backend", f"{BACKEND}/health", "RMI Backend"),
    ("clickhouse", "http://localhost:8123/ping", "ClickHouse"),
    ("qdrant", "http://localhost:6333/health", "Qdrant"),
]


@router.get("/full")
async def full_health_check():
    """Comprehensive system health check — all services."""
    results = {}
    all_healthy = True

    async with httpx.AsyncClient(timeout=5) as c:
        for name, url, label in CHECKS:
            try:
                r = await c.get(url)
                healthy = r.status_code < 500
            except Exception:
                healthy = False

            results[name] = {"service": label, "healthy": healthy, "last_checked": datetime.now(UTC).isoformat()}
            if not healthy:
                all_healthy = False

    # System resources
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    return {
        "overall": "HEALTHY" if all_healthy else "DEGRADED",
        "timestamp": datetime.now(UTC).isoformat(),
        "services": results,
        "system": {
            "cpu_pct": psutil.cpu_percent(interval=1),
            "memory_pct": mem.percent,
            "memory_available_gb": round(mem.available / (1024**3), 1),
            "disk_pct": disk.percent,
            "disk_free_gb": round(disk.free / (1024**3), 1),
            "swap_pct": psutil.swap_memory().percent,
        },
        "alerts": _check_alerts(mem, disk),
    }


def _check_alerts(mem, disk) -> list:
    """Generate alerts for concerning metrics."""
    alerts = []
    if mem.percent > 90:
        alerts.append({"level": "CRITICAL", "msg": f"Memory usage at {mem.percent}% — consider restarting services"})
    elif mem.percent > 80:
        alerts.append({"level": "WARNING", "msg": f"Memory usage at {mem.percent}%"})
    if disk.percent > 90:
        alerts.append({"level": "CRITICAL", "msg": f"Disk usage at {disk.percent}% — prune logs immediately"})
    elif disk.percent > 80:
        alerts.append({"level": "WARNING", "msg": f"Disk usage at {disk.percent}%"})
    return alerts


@router.post("/heal/{service}")
async def heal_service(service: str):
    """Attempt to heal a failed service."""
    if service == "backend":
        os.system("docker restart rmi-backend")
        return {"service": service, "action": "restarted", "note": "Backend restart initiated"}
    if service == "redis":
        os.system("docker restart rmi-redis")
        return {"service": service, "action": "restarted"}
    return {"service": service, "action": "unknown", "note": "Manual intervention required"}
