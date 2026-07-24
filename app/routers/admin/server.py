"""
Server admin endpoints (processes, disk, network deep-dive).
"""

from __future__ import annotations

import logging
from typing import Any

import psutil
from fastapi import APIRouter, Depends, Request

from . import verify_admin

logger = logging.getLogger(__name__)

server_router = APIRouter()


@server_router.get("/processes")
async def admin_processes(
    request: Request, _=Depends(verify_admin)
) -> dict | dict[str, str]:
    """Get running processes on the server."""
    try:
        processes = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                processes.append({
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "cpu": round(proc.info["cpu_percent"] or 0, 1),
                    "memory": round(proc.info["memory_percent"] or 0, 1),
                    "status": proc.info["status"],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        processes.sort(key=lambda p: p["memory"], reverse=True)
        return {"processes": processes[:50], "total": len(processes)}
    except Exception as e:
        return {"error": str(e)}


@server_router.get("/disk")
async def admin_disk(
    request: Request, _=Depends(verify_admin)
) -> dict | dict[str, str]:
    """Get disk usage information."""
    try:
        disks = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "mount": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024**3), 1),
                    "used_gb": round(usage.used / (1024**3), 1),
                    "free_gb": round(usage.free / (1024**3), 1),
                    "percent": usage.percent,
                })
            except PermissionError:
                pass
        return {"disks": disks}
    except Exception as e:
        return {"error": str(e)}


@server_router.get("/network")
async def admin_network(
    request: Request, _=Depends(verify_admin)
) -> dict | dict[str, str]:
    """Get network I/O statistics."""
    try:
        net = psutil.net_io_counters()
        return {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
            "errors_in": net.errin,
            "errors_out": net.errout,
        }
    except Exception as e:
        return {"error": str(e)}
