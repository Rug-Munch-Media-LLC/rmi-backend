"""
Admin Backend — System Health Monitor
=======================================
Monitor system health and performance (CPU, memory, disk, services).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("rmi_admin_backend")


class SystemHealthMonitor:
    """Monitor system health and performance."""

    @staticmethod
    async def get_system_health() -> dict[str, Any]:
        """Get comprehensive system health."""
        import psutil

        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_count = psutil.cpu_count()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net_io = psutil.net_io_counters()
        backend_pid = os.getpid()
        backend_proc = psutil.Process(backend_pid)

        services = {
            "redis": await SystemHealthMonitor._check_redis(),
            "supabase": await SystemHealthMonitor._check_supabase(),
            "backend": {"status": "running", "pid": backend_pid},
        }

        swap = psutil.swap_memory()

        # Top memory consumers — surfaces runaway LSP servers / model servers
        # (2026-08-22 incident: 6 orphaned pyright processes ate ~21GB).
        hogs: list[dict[str, Any]] = []
        for proc in psutil.process_iter():
            try:
                hogs.append(
                    {
                        "pid": proc.pid,
                        "name": (proc.name() or "?")[:40],
                        "mem_mb": round(proc.memory_info().rss / (1024**2), 1),
                        "cpu_percent": proc.cpu_percent(interval=0),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        hogs.sort(key=lambda h: h["mem_mb"], reverse=True)

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "cpu": {
                "percent": cpu_percent,
                "count": cpu_count,
                "per_cpu": psutil.cpu_percent(percpu=True, interval=0.1),
            },
            "memory": {
                "total_gb": round(memory.total / (1024**3), 2),
                "available_gb": round(memory.available / (1024**3), 2),
                "percent": memory.percent,
                "used_gb": round(memory.used / (1024**3), 2),
            },
            "swap": {
                "total_gb": round(swap.total / (1024**3), 2),
                "used_gb": round(swap.used / (1024**3), 2),
                "percent": swap.percent,
            },
            "top_processes": hogs[:10],
            "disk": {
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
                "percent": round(disk.used / disk.total * 100, 1),
            },
            "network": {
                "bytes_sent_mb": round(net_io.bytes_sent / (1024**2), 2),
                "bytes_recv_mb": round(net_io.bytes_recv / (1024**2), 2),
            },
            "backend": {
                "pid": backend_pid,
                "memory_mb": round(backend_proc.memory_info().rss / (1024**2), 2),
                "cpu_percent": backend_proc.cpu_percent(interval=0.2),
                "threads": backend_proc.num_threads(),
                "open_files": len(backend_proc.open_files()),
                "connections": len(backend_proc.connections()),
            },
            "services": services,
            "uptime_seconds": int(time.time() - psutil.boot_time()),
        }

    @staticmethod
    async def _check_redis() -> dict[str, Any]:
        """Check Redis connection."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            await r.ping()
            info = await r.info()
            return {
                "status": "connected",
                "version": info.get("redis_version", "unknown"),
                "used_memory_mb": round(info.get("used_memory", 0) / (1024**2), 2),
                "connected_clients": info.get("connected_clients", 0),
                "total_keys": info.get("db0", {}).get("keys", 0) if isinstance(info.get("db0"), dict) else 0,
            }
        except (httpx.HTTPError, ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
            logger.debug("_check_redis_error", exc_info=True)
            return {"status": "error", "error": str(e)}

    @staticmethod
    async def _check_supabase() -> dict[str, Any]:
        """Check Supabase connection."""
        try:
            supabase_url = os.getenv("SUPABASE_URL")
            if not supabase_url:
                return {"status": "not_configured"}

            async with httpx.AsyncClient() as client:
                r = await client.get(f"{supabase_url}/rest/v1/", timeout=5)
                return {
                    "status": "connected" if r.status_code < 500 else "error",
                    "url": supabase_url,
                    "response_code": r.status_code,
                }
        except (httpx.HTTPError, KeyError, TypeError, ConnectionError, OSError, TimeoutError) as e:
            logger.debug("_check_supabase_error", exc_info=True)
            return {"status": "error", "error": str(e)}
