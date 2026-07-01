#!/usr/bin/env python3
"""
Email API Router - Maildir + SMTP access for all RMI email accounts.
Maildir is mounted from host at /var/mail/vhosts.
SMTP uses host gateway for outbound delivery.
"""

import email
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/email", tags=["email"])

SMTP_HOST = os.getenv("SMTP_HOST", "172.20.0.1")
SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
MAILDIR_BASE = "/var/mail/vhosts"

ACCOUNTS = {
    "admin": "admin@rugmunch.io",
    "biz": "biz@rugmunch.io",
    "security": "security@rugmunch.io",
    "hello": "hello@cryptorugmunch.com",
    "media": "media@cryptorugmunch.com",
}


class SendRequest(BaseModel):
    sender_account: str
    to: str
    subject: str
    body: str


def _maildir_for(account: str) -> str:
    addr = ACCOUNTS.get(account)
    if not addr:
        raise HTTPException(404, f"Unknown account: {account}")
    local, domain = addr.split("@")
    return f"{MAILDIR_BASE}/{domain}/{local}"


def _read_msg(filepath: str, msg_id: int, full: bool = False) -> dict:
    with open(filepath, "rb") as f:
        msg = email.message_from_binary_file(f)
    result = {
        "id": msg_id,
        "subject": str(msg.get("Subject", "")),
        "sender": str(msg.get("From", "")),
        "recipient": str(msg.get("To", "")),
        "date": str(msg.get("Date", "")),
    }
    if full:
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode(errors="replace")
                    break
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode(errors="replace")
        result["body"] = body[:10000]
    return result


def _list_files(maildir: str) -> list:
    files = []
    for sub in ["new", "cur"]:
        d = os.path.join(maildir, sub)
        if os.path.isdir(d):
            files.extend(os.path.join(d, f) for f in os.listdir(d))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


@router.get("/accounts")
def list_accounts():
    return {"accounts": dict(ACCOUNTS.items())}


@router.get("/{account}/inbox")
def list_inbox(account: str, limit: int = Query(20, le=100)):
    if account not in ACCOUNTS:
        raise HTTPException(404, f"Unknown account. Options: {list(ACCOUNTS.keys())}")
    try:
        maildir = _maildir_for(account)
        files = _list_files(maildir)[:limit]
        msgs = [_read_msg(f, i + 1) for i, f in enumerate(files)]
        return {"account": ACCOUNTS[account], "count": len(msgs), "messages": msgs}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{account}/message/{msg_id}")
def read_message(account: str, msg_id: int):
    if account not in ACCOUNTS:
        raise HTTPException(404, "Unknown account")
    try:
        maildir = _maildir_for(account)
        files = _list_files(maildir)
        if msg_id < 1 or msg_id > len(files):
            raise HTTPException(404, "Message not found")
        return _read_msg(files[msg_id - 1], msg_id, full=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/send")
def send_email(req: SendRequest):
    if req.sender_account not in ACCOUNTS:
        raise HTTPException(404, "Unknown sender account")
    sender_addr = ACCOUNTS[req.sender_account]
    msg = MIMEMultipart()
    msg["From"] = sender_addr
    msg["To"] = req.to
    msg["Subject"] = req.subject
    msg.attach(MIMEText(req.body, "plain"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.send_message(msg)
        return {"status": "sent", "from": sender_addr, "to": req.to}
    except Exception as e:
        raise HTTPException(500, f"Send failed: {e}")
