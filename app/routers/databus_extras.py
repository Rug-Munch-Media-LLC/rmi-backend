"""#2 SSE Streaming + #3 Provider Dashboard endpoints."""

import os

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/databus", tags=["databus-extras"])

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")


@router.get("/providers/dashboard")
async def provider_dashboard():
    """Real-time provider health dashboard data — feed into Grafana."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{BACKEND}/api/v1/databus/providers/health",
                headers={"X-RMI-Key": os.getenv("RMI_INTERNAL_KEY", "rmi-internal-2026")},
            )
            if r.status_code == 200:
                data = r.json()
                providers = data.get("providers", data)

                # Format for Grafana
                panels = []
                for name, health in providers.items() if isinstance(providers, dict) else []:
                    panels.append(
                        {
                            "provider": name,
                            "status": "healthy" if health.get("healthy", True) else "degraded",
                            "latency_ms": health.get("avg_latency_ms", 0),
                            "error_rate": health.get("error_rate", 0),
                            "circuit": health.get("circuit_state", "closed"),
                        }
                    )

                return {
                    "providers": panels,
                    "summary": {
                        "total": len(panels),
                        "healthy": sum(1 for p in panels if p["status"] == "healthy"),
                        "degraded": sum(1 for p in panels if p["status"] != "healthy"),
                    },
                }
    except Exception:
        pass

    return {"providers": [], "note": "Provider health API unavailable — check backend"}


@router.get("/queue/stats")
async def task_queue_stats():
    """Background task queue statistics."""
    from app.core.task_queue import get_queue_stats

    return await get_queue_stats()
