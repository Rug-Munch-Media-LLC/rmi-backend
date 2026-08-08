"""
Webmail admin endpoints.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from . import verify_admin

logger = logging.getLogger(__name__)

mail_router = APIRouter()


@mail_router.get("/mail/sent")
async def mail_sent(request: Request, _=Depends(verify_admin), limit: int = Query(20, le=100)) -> dict[str, list[Any]]:
    """Get recently sent emails."""
    return {"messages": [], "total": 0, "source": "smtp_tracking"}


@mail_router.get("/mail/folders")
async def mail_folders(request: Request, _=Depends(verify_admin)) -> dict[str, list[dict[str, str]]]:
    """Get email folders."""
    return {
        "folders": [
            {"name": "INBOX", "count": 0, "unread": 0},
            {"name": "Sent", "count": 0, "unread": 0},
            {"name": "Drafts", "count": 0, "unread": 0},
            {"name": "Spam", "count": 0, "unread": 0},
            {"name": "Trash", "count": 0, "unread": 0},
        ]
    }


@mail_router.delete("/mail/{msg_id}")
async def mail_delete(request: Request, msg_id: str, _=Depends(verify_admin)) -> dict[str, str]:
    """Delete an email — requires SMTP sent tracking."""
    return {
        "status": "acknowledged",
        "id": msg_id,
        "source": "smtp_tracking",
        "note": "Deletion requires SMTP sent tracking — email may not have been actually deleted",
    }
