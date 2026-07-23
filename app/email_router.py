"""
Email Service - Listmonk + Gmail SMTP integration for rugmunch.io
Uses listmonk API for newsletters + transactional emails.
SMTP relay: Gmail (cryptorugmuncher@gmail.com)
"""

import os
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from typing import Any

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/email", tags=["email"])

LISTMONK_URL = "http://127.0.0.1:9001/api"
LISTMONK_USER = "admin"
LISTMONK_PASS = "rugmunch2024"

_token_cache = {"token": None, "expires": 0}


async def _get_token() -> str:
    """Get listmonk API token, cached."""
    import time

    if _token_cache["token"] and time.time() < _token_cache["expires"]:
        return _token_cache["token"]

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"{LISTMONK_URL}/auth/login",
            json={"username": LISTMONK_USER, "password": LISTMONK_PASS},
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            token = data.get("token", "")
            _token_cache["token"] = token
            _token_cache["expires"] = time.time() + 3600
            return token
    raise HTTPException(status_code=500, detail="Listmonk auth failed")


# ═══════════════════════════════════════════════════════════
# SUBSCRIBERS
# ═══════════════════════════════════════════════════════════
@router.get("/subscribers")
async def list_subscribers() -> Any:
    token = await _get_token()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{LISTMONK_URL}/subscribers", headers={"Authorization": f"Bearer {token}"})
        return r.json()


@router.post("/subscribers")
async def add_subscriber(data: dict) -> Any:
    email = data.get("email", "")
    name = data.get("name", "")
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    token = await _get_token()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"{LISTMONK_URL}/subscribers",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": email, "name": name, "status": "enabled"},
        )
        return r.json()


# ═══════════════════════════════════════════════════════════
# NEWSLETTER CAMPAIGNS
# ═══════════════════════════════════════════════════════════
@router.post("/campaign")
async def create_campaign(data: dict) -> Any:
    subject = data.get("subject", "RMI Intelligence Update")
    body = data.get("body", "")
    list_ids = data.get("list_ids", [1])
    if not body:
        raise HTTPException(status_code=400, detail="body required")
    token = await _get_token()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"{LISTMONK_URL}/campaigns",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": subject,
                "subject": subject,
                "lists": list_ids,
                "content_type": "html",
                "body": body,
                "send_at": None,
            },
        )
        return r.json()


@router.get("/campaigns")
async def list_campaigns() -> Any:
    token = await _get_token()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{LISTMONK_URL}/campaigns", headers={"Authorization": f"Bearer {token}"})
        return r.json()


# ═══════════════════════════════════════════════════════════
# TRANSACTIONAL - Direct Gmail SMTP
# ═══════════════════════════════════════════════════════════
@router.post("/send")
async def send_transactional(data: dict) -> dict[str, str]:
    """Send a single transactional email via Gmail SMTP."""
    to = data.get("to", "")
    subject = data.get("subject", "RMI Alert")
    body = data.get("body", "")
    if not to or not body:
        raise HTTPException(status_code=400, detail="to and body required")

    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart()
        msg["From"] = "Rug Munch Intelligence <cryptorugmuncher@gmail.com>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.starttls()
            # Gmail App Password needed - stored in env
            app_password = os.getenv("GMAIL_APP_PASSWORD", "")
            server.login("cryptorugmuncher@gmail.com", app_password)
            server.send_message(msg)

        return {
            "status": "sent",
            "to": to,
            "subject": subject,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
            "hint": "Gmail App Password required. Set GMAIL_APP_PASSWORD env var",
        }


@router.get("/status")
async def email_status() -> dict[str, str]:
    return {
        "provider": "listmonk + gmail",
        "smtp": "smtp.gmail.com:587",
        "sender": "cryptorugmuncher@gmail.com",
        "domain": "rugmunch.io",
        "listmonk_url": "http://localhost:9001",
        "newsletter_enabled": True,
        "transactional_enabled": True,
    }
