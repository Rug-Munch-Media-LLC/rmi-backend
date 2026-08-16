"""
Server admin endpoints (processes, disk, network deep-dive, system updates).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

import psutil
from fastapi import APIRouter, Depends, Request

from . import verify_admin

logger = logging.getLogger(__name__)

server_router = APIRouter()


@server_router.get("/processes")
async def admin_processes(request: Request, _=Depends(verify_admin)) -> dict[str, Any] | dict[str, str]:
    """Get running processes on the server."""
    try:
        processes = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                processes.append(
                    {
                        "pid": proc.info["pid"],  # psutil Process.info is a dynamic per-iter attrs dict
                        "name": proc.info["name"],
                        "cpu": round(proc.info["cpu_percent"] or 0, 1),
                        "memory": round(proc.info["memory_percent"] or 0, 1),
                        "status": proc.info["status"],
                    }
                )
        processes.sort(key=lambda p: p["memory"], reverse=True)
        return {"processes": processes[:50], "total": len(processes)}
    except Exception as e:
        return {"error": str(e)}


@server_router.get("/disk")
async def admin_disk(request: Request, _=Depends(verify_admin)) -> dict[str, Any] | dict[str, str]:
    """Get disk usage information."""
    try:
        disks = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append(
                    {
                        "mount": part.mountpoint,
                        "fstype": part.fstype,
                        "total_gb": round(usage.total / (1024**3), 1),
                        "used_gb": round(usage.used / (1024**3), 1),
                        "free_gb": round(usage.free / (1024**3), 1),
                        "percent": usage.percent,
                    }
                )
            except PermissionError:
                pass
        return {"disks": disks}
    except Exception as e:
        return {"error": str(e)}


@server_router.get("/network")
async def admin_network(request: Request, _=Depends(verify_admin)) -> dict[str, Any] | dict[str, str]:
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


@server_router.get("/updates")
async def admin_updates(request: Request, _=Depends(verify_admin)) -> dict[str, Any]:
    """Get system update status — Docker, packages, Python deps, system health.

    Delegates to the system-updates-check.sh script and augments with
    real-time psutil data. No external API calls — all local checks.
    """
    result: dict[str, Any] = {
        "status": "ok",
        "system_packages": 0,
        "python_packages": 0,
        "uptime": "unknown",
        "load_avg": "0 0 0",
        "memory": {"total_mb": 0, "used_mb": 0, "percent": 0},
        "docker": {"running": 0, "stopped": 0, "images_total": 0, "images_latest_tag": 0},
        "last_backup": "unknown",
        "docker_images": [],
        "top_images": [],
    }

    # ── Try running the system-updates-check.sh script ──
    script_path = "[INTERNAL]/system-updates-check.sh"
    if os.path.exists(script_path):
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if stdout:
                script_data = json.loads(stdout.decode())
                result.update(script_data)
        except TimeoutError:
            logger.warning("admin_updates: system-updates-check.sh timed out")
            result["status"] = "partial"
        except (json.JSONDecodeError, FileNotFoundError, PermissionError) as exc:
            logger.warning("admin_updates: script error: %s", exc)
            result["status"] = "partial"

    # ── Augment with live psutil data (always available) ──
    try:
        mem = psutil.virtual_memory()
        result["memory"]["total_mb"] = round(mem.total / (1024**2), 1)
        result["memory"]["used_mb"] = round(mem.used / (1024**2), 1)
        result["memory"]["percent"] = mem.percent
        load_avg = psutil.getloadavg()
        result["load_avg"] = f"{load_avg[0]:.2f} {load_avg[1]:.2f} {load_avg[2]:.2f}"
        result["uptime"] = f"{round(psutil.boot_time())}"
    except Exception as exc:
        logger.warning("admin_updates: psutil error: %s", exc)

    # ── Docker images snapshot (top 15 by size) ──
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "images",
            "--format",
            "{{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.ID}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if stdout:
            lines = stdout.decode().strip().split("\n")
            images = []
            for line in lines:
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    images.append(
                        {
                            "name": parts[0],
                            "size": parts[1],
                            "id": parts[2] if len(parts) > 2 else "",
                        }
                    )
            result["docker_images"] = images

            def _size_sort(img: dict[str, Any]) -> float:
                s = img["size"]
                if "GB" in s:
                    return float(s.replace("GB", "").strip()) * 1024
                if "MB" in s:
                    return float(s.replace("MB", "").strip())
                if "kB" in s:
                    return float(s.replace("kB", "").strip()) / 1024
                return 0

            images_sorted = sorted(images, key=_size_sort, reverse=True)
            result["top_images"] = images_sorted[:15]
    except TimeoutError:
        logger.warning("admin_updates: docker images timed out")
        result["status"] = "partial"
    except (FileNotFoundError, PermissionError) as exc:
        logger.warning("admin_updates: docker error: %s", exc)
        result["status"] = "partial"

    # ── Python outdated packages detail (top 20) ──
    try:
        proc = await asyncio.create_subprocess_exec(
            "pip3",
            "list",
            "--outdated",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        if stdout:
            pypi_lines = stdout.decode().strip().split("\n")
            pkg_list = []
            for line in pypi_lines[2:]:  # Skip header
                parts = line.split()
                if len(parts) >= 3:
                    pkg_list.append(
                        {
                            "name": parts[0],
                            "current": parts[1],
                            "latest": parts[2],
                        }
                    )
            result["python_packages_detail"] = pkg_list[:20]
            result["python_packages"] = len(pkg_list)
    except (TimeoutError, FileNotFoundError):
        pass

    return result
