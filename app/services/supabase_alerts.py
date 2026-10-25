"""Supabase alerts table: read, create, acknowledge."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.services.supabase_transport import (
    _get_headers,
    _patch,
    _post,
    _url,
)


async def get_alerts(
    severity: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
    unread_only: bool = False,
) -> list[Any]:
    params = {}
    if severity:
        params["severity"] = severity
    if user_id:
        params["user_id"] = user_id
    order = "created_at.desc"

    url = _url("alerts")
    query_parts = ["select=*", f"order={order}", f"limit={limit}"]
    for k, v in params.items():
        query_parts.append(f"{k}=eq.{v}")
    if unread_only:
        query_parts.append("is_read=eq.false")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def create_alert(
    title: str,
    description: str,
    severity: str = "medium",
    alert_type: str = "security",
    chain: str | None = None,
    token: str | None = None,
    wallet: str | None = None,
    amount_usd: float | None = None,
    tx_hash: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    data: dict[str, Any] = {
        "title": title,
        "description": description,
        "severity": severity,
        "type": alert_type,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if chain:
        data["chain"] = chain
    if token:
        data["token"] = token
    if wallet:
        data["wallet"] = wallet
    if amount_usd:
        data["amount_usd"] = amount_usd
    if tx_hash:
        data["tx_hash"] = tx_hash
    if user_id:
        data["user_id"] = user_id
    if metadata:
        data["metadata"] = metadata

    return await _post("alerts", data)


async def acknowledge_alert(alert_id: str) -> bool:
    return await _patch("alerts", {"id": alert_id}, {"acknowledged": True, "is_read": True})
