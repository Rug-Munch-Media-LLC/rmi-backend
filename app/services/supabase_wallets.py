"""Supabase wallets: wallets, wallet intel, syndicates, watchlist."""

from __future__ import annotations

from typing import Any

import httpx

from app.services.supabase_transport import (
    _delete,
    _get,
    _get_headers,
    _post,
    _url,
)


async def get_wallets(
    chain: str | None = None,
    risk_min: int | None = None,
    risk_max: int | None = None,
    limit: int = 50,
) -> list[Any]:
    url = _url("wallets")
    query_parts = ["select=*", f"limit={limit}"]
    if chain:
        query_parts.append(f"chain=eq.{chain}")
    if risk_min is not None:
        query_parts.append(f"risk_score=gte.{risk_min}")
    if risk_max is not None:
        query_parts.append(f"risk_score=lte.{risk_max}")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def get_wallet(address: str, chain: str = "ethereum") -> dict[str, Any] | None:
    url = f"{_url('wallets')}?select=*&address=eq.{address}&chain=eq.{chain}&limit=1"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        data = r.json() if r.status_code == 200 else []
        return data[0] if data else None


async def upsert_wallet(address: str, chain: str, **fields) -> dict[str, Any] | None:
    data = {"address": address, "chain": chain, **fields}
    url = f"{_url('wallets')}"
    headers = dict(_get_headers())
    headers["Prefer"] = "resolution=merge-duplicates"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=data, headers=headers)
        return r.json()[0] if r.status_code in (200, 201) and r.json() else None


async def get_wallet_intel(address: str | None = None, chain: str | None = None, limit: int = 100) -> list[Any]:
    url = _url("wallet_intel")
    query_parts = ["select=*", f"limit={limit}"]
    if address:
        query_parts.append(f"address=eq.{address}")
    if chain:
        query_parts.append(f"chain=eq.{chain}")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def get_wallet_connections(address: str) -> list[Any]:
    """Find all wallets connected to this address."""
    url = f"{_url('wallet_intel')}?select=*&related_addresses=cs.{{'{address}'}}&limit=50"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def get_syndicate_wallets(status: str | None = None, chain: str | None = None, limit: int = 200) -> list[Any]:
    url = _url("syndicate_wallets")
    query_parts = ["select=*", f"limit={limit}"]
    if status:
        query_parts.append(f"status=eq.{status}")
    if chain:
        query_parts.append(f"chain=eq.{chain}")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def get_watchlist(user_id: str) -> list[Any]:
    return await _get("watchlist", {"user_id": user_id}, order="created_at.desc")


async def add_to_watchlist(
    user_id: str,
    address: str,
    chain: str = "ethereum",
    tags: list[Any] | None = None,
    item_type: str = "wallet",
) -> dict[str, Any] | None:
    """Add an address to a user's watchlist. item_type: 'token' or 'wallet'."""
    return await _post(
        "watchlist",
        {
            "user_id": user_id,
            "address": address,
            "type": item_type,
            "chain": chain,
            "tags": tags or [],
        },
    )


async def remove_from_watchlist(user_id: str, address: str, chain: str = "ethereum") -> bool:
    return await _delete(
        "watchlist",
        {
            "user_id": user_id,
            "address": address,
            "chain": chain,
        },
    )
