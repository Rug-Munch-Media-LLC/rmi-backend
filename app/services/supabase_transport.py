"""Supabase REST transport: generic table/RPC helpers (service_role)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.secrets import get_secret_or_none


def _get_url() -> str:
    return os.environ.get("SUPABASE_URL", "")


def _get_key() -> str:
    """Get Supabase service key — required for full backend CRUD access.

    Service role key provides unrestricted database access (bypasses RLS).
    Required because:
    - Backend performs admin operations (upserts, deletes, bulk inserts)
    - Executes raw SQL via RPC for index creation and migrations
    - Reads sensitive tables (wallet_intel, x402_payments)

    Falls back from SUPABASE_SERVICE_KEY to SUPABASE_SERVICE_ROLE_KEY
    for backward compatibility.
    """
    # Try SUPABASE_SERVICE_KEY first (primary)
    key = get_secret_or_none("SUPABASE_SERVICE_KEY", "")
    if key:
        return key
    # Fall back to SUPABASE_SERVICE_ROLE_KEY (legacy naming)
    key = get_secret_or_none("SUPABASE_SERVICE_ROLE_KEY")
    if key:
        return key
    return ""


def _get_headers() -> dict[str, Any]:
    key = _get_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _url(table: str) -> str:
    return f"{_get_url()}/rest/v1/{table}"


async def _get(
    table: str,
    params: dict[str, Any] | None = None,
    select: str = "*",
    order: str | None = None,
    limit: int | None = None,
) -> list[Any]:
    """Generic GET query."""
    url = _url(table)
    query_parts = [f"select={select}"]
    if params:
        for k, v in params.items():
            query_parts.append(f"{k}=eq.{v}")
    if order:
        query_parts.append(f"order={order}")
    if limit:
        query_parts.append(f"limit={limit}")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        if r.status_code == 200:
            return r.json()
        return []


async def _get_in(table: str, field: str, values: list[Any], select: str = "*") -> list[Any]:
    """GET with IN filter."""
    vals = ",".join(str(v) for v in values)
    url = f"{_url(table)}?select={select}&{field}=in.({vals})"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def _post(table: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Insert a row."""
    url = f"{_url(table)}"
    headers = dict(_get_headers())
    headers["Prefer"] = "return=representation"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=data, headers=headers)
        if r.status_code in (200, 201):
            return r.json()[0] if r.json() else {}
        return None


async def _patch(table: str, match: dict[str, Any], data: dict[str, Any]) -> bool:
    """Update row(s)."""
    url = _url(table)
    query_parts = [f"{k}=eq.{v}" for k, v in match.items()]
    url += "?" + "&".join(query_parts)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.patch(url, json=data, headers=_get_headers())
        return r.status_code in (200, 204)


async def _delete(table: str, match: dict[str, Any]) -> bool:
    """Delete row(s)."""
    url = _url(table)
    query_parts = [f"{k}=eq.{v}" for k, v in match.items()]
    url += "?" + "&".join(query_parts)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(url, headers=_get_headers())
        return r.status_code in (200, 204)


async def _rpc(fn: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Call a stored procedure."""
    url = f"{_get_url()}/rest/v1/rpc/{fn}"
    headers = dict(_get_headers())
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=params or {}, headers=headers)
        return r.json() if r.status_code == 200 else None
