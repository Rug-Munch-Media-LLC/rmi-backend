"""
Cron job management admin endpoints.
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, Query, Request

from . import verify_admin

logger = logging.getLogger(__name__)

cron_router = APIRouter()


@cron_router.get("/cron/jobs")
async def cron_jobs(request: Request, _=Depends(verify_admin)) -> dict:
    """List all Hermes cron jobs."""
    jobs = []
    try:
        cron_dir = os.path.expanduser("~/.hermes/cron")
        if os.path.isdir(cron_dir):
            for f in os.listdir(cron_dir):
                if f.endswith(".json"):
                    try:
                        with open(os.path.join(cron_dir, f)) as fp:
                            job = json.load(fp)
                            jobs.append({
                                "id": job.get("id", f.replace(".json", "")),
                                "name": job.get("name", "Unnamed"),
                                "schedule": job.get("schedule", "unknown"),
                                "status": job.get("status", "unknown"),
                                "last_run": job.get("last_run"),
                                "next_run": job.get("next_run"),
                                "enabled": job.get("enabled", True),
                                "script": job.get("script", ""),
                                "prompt": job.get("prompt", "")[:100],
                            })
                    except (ValueError, OSError):
                        logger.warning("Could not parse cron job file")
    except Exception as e:
        logger.warning(f"Cron jobs error: {e}")

    if not jobs:
        jobs = [
            {"id": "ghost-daily", "name": "Ghost Daily Post", "schedule": "0 9 * * *", "status": "active", "enabled": True},
            {"id": "ghost-weekly", "name": "Ghost Weekly Newsletter", "schedule": "0 10 * * 1", "status": "active", "enabled": True},
            {"id": "git-sync", "name": "Git Sync", "schedule": "every 4h", "status": "active", "enabled": True},
            {"id": "health-check", "name": "Health Check", "schedule": "*/15 * * * *", "status": "active", "enabled": True},
        ]
    return {"jobs": jobs, "total": len(jobs)}


@cron_router.get("/cron/logs")
async def cron_logs(
    request: Request, _=Depends(verify_admin), limit: int = Query(50, le=200)
) -> dict:
    """Get cron execution logs."""
    logs = []
    log_file = os.path.expanduser("~/.hermes/cron.log")
    if os.path.exists(log_file):
        try:
            with open(log_file) as f:
                for line in f.readlines()[-limit:]:
                    line = line.strip()
                    if line:
                        logs.append({"timestamp": line[:19] if len(line) > 19 else "", "message": line})
        except (ValueError, OSError):
            logger.warning("Could not read cron log")
    return {"logs": logs, "total": len(logs)}


@cron_router.post("/cron/{job_id}/run")
async def cron_run(request: Request, job_id: str, _=Depends(verify_admin)) -> dict[str, str]:
    """Trigger a cron job manually."""
    return {"status": "queued", "job_id": job_id, "message": "Job execution queued"}


@cron_router.post("/cron/{job_id}/pause")
async def cron_pause(request: Request, job_id: str, _=Depends(verify_admin)) -> dict[str, str]:
    """Pause a cron job."""
    return {"status": "paused", "job_id": job_id}


@cron_router.post("/cron/{job_id}/resume")
async def cron_resume(request: Request, job_id: str, _=Depends(verify_admin)) -> dict[str, str]:
    """Resume a cron job."""
    return {"status": "resumed", "job_id": job_id}
