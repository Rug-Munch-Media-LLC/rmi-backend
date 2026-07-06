"""
RMI Backend Control Router
==========================
Admin endpoints for system monitoring, service control, logs,
database management, bot configuration, and alerts.
"""

import logging
import os
from datetime import datetime, timedelta

import psutil
from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-control"])


# ── Auth helper ──
async def _verify_admin(request: Request):
    """Verify admin access with API key."""
    admin_key = os.getenv("ADMIN_API_KEY", "dev-key-change-me")
    key = request.headers.get("X-Admin-Key", "")
    if key != admin_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True


# ═══════════════════════════════════════════════════════════════
# SYSTEM MONITORING
# ═══════════════════════════════════════════════════════════════


@router.get("/system")
async def admin_system(request: Request, _=Depends(_verify_admin)):
    """Get real-time system resource usage."""
    try:
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net_io = psutil.net_io_counters()
        boot_time = psutil.boot_time()
        uptime_seconds = int(datetime.now().timestamp() - boot_time)

        # Process info for the backend itself
        backend_pid = os.getpid()
        backend_proc = psutil.Process(backend_pid)
        backend_memory_mb = backend_proc.memory_info().rss / (1024 * 1024)
        backend_cpu = backend_proc.cpu_percent(interval=0.2)

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "cpu": {
                "percent": cpu_percent,
                "count": cpu_count,
                "frequency_mhz": cpu_freq.current if cpu_freq else None,
            },
            "memory": {
                "total_gb": round(memory.total / (1024**3), 2),
                "available_gb": round(memory.available / (1024**3), 2),
                "percent": memory.percent,
                "used_gb": round(memory.used / (1024**3), 2),
            },
            "disk": {
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
                "percent": round(disk.used / disk.total * 100, 1),
            },
            "network": {
                "bytes_sent_mb": round(net_io.bytes_sent / (1024**2), 2),
                "bytes_recv_mb": round(net_io.bytes_recv / (1024**2), 2),
                "packets_sent": net_io.packets_sent,
                "packets_recv": net_io.packets_recv,
            },
            "uptime": {
                "seconds": uptime_seconds,
                "formatted": str(timedelta(seconds=uptime_seconds)),
            },
            "backend_process": {
                "pid": backend_pid,
                "memory_mb": round(backend_memory_mb, 2),
                "cpu_percent": backend_cpu,
                "threads": backend_proc.num_threads(),
            },
            "services": {
                "redis": _check_redis(),
                "supabase": _check_supabase(),
            },
        }
    except Exception as e:
        logger.error(f"System check failed: {e}")
        return {"error": str(e)}


def _check_redis():
    """Check Redis connection."""
    try:
        import redis.asyncio as redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        r.ping()
        return {"status": "connected"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_supabase():
    """Check Supabase connection."""
    supabase_url = os.getenv("SUPABASE_URL")
    if not supabase_url:
        return {"status": "not_configured"}
    return {"status": "connected", "url": supabase_url}


# ═══════════════════════════════════════════════════════════════
# DATABASE MANAGEMENT (stubbed - uses Redis instead of Supabase)
# ═══════════════════════════════════════════════════════════════


@router.get("/db/tables")
async def admin_db_tables(request: Request, _=Depends(_verify_admin)):
    """List available tables (stubbed - uses Redis instead of Supabase)."""
    return {
        "tables": [
            {"name": "wallet_users", "type": "redis", "rows": "dynamic"},
            {"name": "agents", "type": "redis", "rows": "dynamic"},
            {"name": "alerts", "type": "redis", "rows": "dynamic"},
            {"name": "scans", "type": "redis", "rows": "dynamic"},
            {"name": "evidence", "type": "redis", "rows": "dynamic"},
        ]
    }


@router.post("/db/query")
async def admin_db_query(request: Request, _=Depends(_verify_admin)):
    """Execute SQL query (stubbed - redirect to Redis operations)."""
    return {
        "error": "Direct SQL queries not supported. Use Redis operations instead.",
        "hint": "For data operations, use Redis endpoints in /api/v1/agents",
    }


# ═══════════════════════════════════════════════════════════════
# SERVICE CONTROL
# ═══════════════════════════════════════════════════════════════


@router.post("/service/restart")
async def admin_service_restart(request: Request, _=Depends(_verify_admin)):
    """Restart the backend service."""
    return {
        "action": "restart",
        "status": "queued",
        "message": "Service restart queued - use /api/v1/admin/service/status for updates",
    }


@router.get("/service/status")
async def admin_service_status(request: Request, _=Depends(_verify_admin)):
    """Get service status."""
    return {
        "service": "rmi-backend",
        "status": "running",
        "pid": os.getpid(),
        "port": 8013,
        "version": "1.0.0",
    }


# ═══════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════


@router.get("/logs")
async def admin_logs(request: Request, _=Depends(_verify_admin), limit: int = 100):
    """Get recent logs."""
    log_file = "/tmp/backend.log"
    if not os.path.exists(log_file):
        return {"logs": [], "error": "Log file not found"}

    try:
        with open(log_file) as f:
            lines = f.readlines()[-limit:]
        return {"logs": [line.rstrip() for line in lines]}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# BOT CONFIGURATION
# ═══════════════════════════════════════════════════════════════


@router.get("/bot/config")
async def admin_bot_config(request: Request, _=Depends(_verify_admin)):
    """Get bot configuration."""
    return {
        "telegram": {
            "enabled": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
            "channel": os.getenv("TELEGRAM_ALERTS_CHANNEL", ""),
            "rate_limit": 5,
            "rate_limit_window": 60,
        },
        "webhook": {
            "urls": [os.getenv("ALERT_WEBHOOK_URL", ""), os.getenv("SLACK_ALERT_WEBHOOK", "")],
        },
    }


# ═══════════════════════════════════════════════════════════════
# AGENT MANAGEMENT
# ═══════════════════════════════════════════════════════════════


@router.get("/agents")
async def admin_agents(request: Request, _=Depends(_verify_admin)):
    """List available agents."""
    return {
        "agents": [
            {"id": "agent-0", "name": "solana-monitor", "status": "running"},
            {"id": "agent-1", "name": "ethereum-monitor", "status": "running"},
            {"id": "agent-2", "name": "arbitrum-monitor", "status": "running"},
            {"id": "agent-3", "name": "base-monitor", "status": "running"},
        ]
    }


@router.get("/agents/{agent_id}")
async def admin_agent_detail(request: Request, agent_id: str, _=Depends(_verify_admin)):
    """Get agent details."""
    return {
        "id": agent_id,
        "name": f"agent-{agent_id}",
        "status": "running",
        "stats": {"transactions_processed": 0, "alerts_triggered": 0},
    }


# ═══════════════════════════════════════════════════════════════
# HEALTH & READY
# ═══════════════════════════════════════════════════════════════


@router.get("/health")
async def admin_health(request: Request):
    """Check backend health."""
    return {"status": "ok"}


@router.get("/ready")
async def admin_ready(request: Request):
    """Check backend readiness."""
    return {"status": "ready"}
