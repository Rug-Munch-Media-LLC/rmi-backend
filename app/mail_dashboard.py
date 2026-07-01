"""
Email Dashboard — Full email management for rugmunch.io + cryptorugmunch.com
Access cryptorugmuncher@gmail.com via backend (no password needed).
"""

import email as emaillib
import imaplib
import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/mail", tags=["mail"])

# ═══════════════════════════════════════════════════════════
# EMAIL ADDRESSES
# ═══════════════════════════════════════════════════════════
EMAILS = {
    "rugmunch.io": [
        {
            "address": "team@rugmunch.io",
            "purpose": "General inquiries",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
        {
            "address": "alerts@rugmunch.io",
            "purpose": "Security alerts & notifications",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
        {
            "address": "intel@rugmunch.io",
            "purpose": "Intelligence reports",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
        {
            "address": "support@rugmunch.io",
            "purpose": "Customer support",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
        {
            "address": "admin@rugmunch.io",
            "purpose": "Admin/backend",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
        {
            "address": "newsletter@rugmunch.io",
            "purpose": "Weekly/daily newsletter dispatches",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
        {
            "address": "biz@rugmunch.io",
            "purpose": "Business relationships & partnerships",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
        {
            "address": "bot@rugmunch.io",
            "purpose": "Bot/automation account (X/Telegram/backend alerts)",
            "forward_to": "admin@rugmunch.io",
        },
    ],
    "cryptorugmunch.com": [
        {
            "address": "team@cryptorugmunch.com",
            "purpose": "Main contact",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
        {
            "address": "alerts@cryptorugmunch.com",
            "purpose": "Alert system",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
        {
            "address": "intel@cryptorugmunch.com",
            "purpose": "Intel feed",
            "forward_to": "cryptorugmuncher@gmail.com",
        },
    ],
}


@router.get("/addresses")
async def list_addresses():
    """All email addresses across domains."""
    return {
        "domains": list(EMAILS.keys()),
        "addresses": EMAILS,
        "total": sum(len(v) for v in EMAILS.values()),
        "primary_inbox": "cryptorugmuncher@gmail.com",
        "provider": "gmail_smtp + listmonk_newsletter",
    }


# ═══════════════════════════════════════════════════════════
# GMAIL INBOX — Read without password
# ═══════════════════════════════════════════════════════════
@router.get("/inbox")
async def check_inbox(limit: int = 10):
    """Check cryptorugmuncher@gmail.com inbox (requires app password in env)."""
    app_password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not app_password:
        return {
            "emails": [],
            "error": "GMAIL_APP_PASSWORD not set. Get one at https://myaccount.google.com/apppasswords",
            "account": "cryptorugmuncher@gmail.com",
        }

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login("cryptorugmuncher@gmail.com", app_password)
        mail.select("INBOX")

        status, messages = mail.search(None, "ALL")
        email_ids = messages[0].split()[-limit:] if messages[0] else []

        emails = []
        for eid in reversed(email_ids):
            _status, msg_data = mail.fetch(eid, "(RFC822)")
            if msg_data and msg_data[0]:
                raw = emaillib.message_from_bytes(msg_data[0][1])
                emails.append(
                    {
                        "id": eid.decode(),
                        "from": raw.get("From", ""),
                        "subject": raw.get("Subject", ""),
                        "date": raw.get("Date", ""),
                        "snippet": _get_body(raw)[:200],
                    }
                )

        mail.close()
        mail.logout()

        return {
            "account": "cryptorugmuncher@gmail.com",
            "total_inbox": len(email_ids),
            "showing": len(emails),
            "emails": emails,
            "checked_at": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "account": "cryptorugmuncher@gmail.com"}


def _get_body(msg) -> str:
    """Extract text body from email."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(errors="ignore")
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(errors="ignore")
    except Exception:
        pass
    return "(no text content)"


# ═══════════════════════════════════════════════════════════
# SEND — From any rugmunch.io address
# ═══════════════════════════════════════════════════════════
@router.post("/send")
async def send_mail(data: dict):
    """Send email from any rugmunch.io/cryptorugmunch.com address via Gmail SMTP."""
    to = data.get("to", "")
    subject = data.get("subject", "")
    body = data.get("body", "")
    from_addr = data.get("from", "team@rugmunch.io")

    if not to or not body:
        raise HTTPException(status_code=400, detail="to and body required")

    app_password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not app_password:
        return {"status": "failed", "error": "GMAIL_APP_PASSWORD not set"}

    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart()
        msg["From"] = f"Rug Munch Intelligence <{from_addr}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg["Reply-To"] = "team@rugmunch.io"
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.starttls()
            server.login("cryptorugmuncher@gmail.com", app_password)
            server.send_message(msg)

        return {
            "status": "sent",
            "from": from_addr,
            "to": to,
            "subject": subject,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# DOMAINS SETUP
# ═══════════════════════════════════════════════════════════
@router.get("/domains")
async def mail_domains():
    return {
        "domains": [
            {"domain": "rugmunch.io", "status": "active", "mx": "smtp.gmail.com", "emails": 5},
            {
                "domain": "cryptorugmunch.com",
                "status": "active",
                "mx": "smtp.gmail.com",
                "emails": 3,
            },
        ],
        "smtp_provider": "gmail",
        "smtp_account": "cryptorugmuncher@gmail.com",
        "newsletter": "listmonk (localhost:9001)",
        "setup_note": "Add GMAIL_APP_PASSWORD to enable full email functionality",
    }
