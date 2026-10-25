"""Supabase records: profiles, contract analyses, clusters, transactions, market data."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.services.supabase_transport import (
    _get,
    _get_headers,
    _patch,
    _post,
    _url,
)


async def get_profile(user_id: str) -> dict[str, Any] | None:
    data = await _get("profiles", {"id": user_id}, limit=1)
    return data[0] if data else None


async def update_profile(user_id: str, **fields) -> bool:
    return await _patch("profiles", {"id": user_id}, fields)


async def get_contract_analysis(address: str, chain: str = "ethereum") -> dict[str, Any] | None:
    data = await _get("contract_analyses", {"address": address, "chain": chain}, limit=1)
    return data[0] if data else None


async def upsert_contract_analysis(address: str, chain: str, **fields) -> dict[str, Any] | None:
    data = {
        "address": address,
        "chain": chain,
        "analyzed_at": datetime.now(UTC).isoformat(),
        **fields,
    }
    url = f"{_url('contract_analyses')}"
    headers = dict(_get_headers())
    headers["Prefer"] = "resolution=merge-duplicates"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=data, headers=headers)
        return r.json()[0] if r.status_code in (200, 201) and r.json() else None


async def get_entity_clusters(limit: int = 50) -> list[Any]:
    return await _get("entity_clusters", order="created_at.desc", limit=limit)


async def get_cluster_wallets(cluster_id: str) -> list[Any]:
    clusters = await _get("entity_clusters", {"cluster_id": cluster_id}, limit=1)
    if not clusters:
        return []
    addresses = clusters[0].get("addresses", [])
    return addresses


async def get_transactions(wallet_address: str, chain: str | None = None, limit: int = 50) -> list[Any]:
    params = {"wallet_address": wallet_address}
    if chain:
        params["chain"] = chain
    return await _get("wallet_transactions", params, order="timestamp.desc", limit=limit)


async def get_market_data(data_type: str | None = None, limit: int = 10) -> list[Any]:
    params = {}
    if data_type:
        params["data_type"] = data_type
    return await _get("market_data", params, order="fetched_at.desc", limit=limit)


async def store_market_data(source: str, data_type: str, data: dict[str, Any]) -> dict[str, Any] | None:
    return await _post(
        "market_data",
        {
            "source": source,
            "data_type": data_type,
            "data": data,
            "fetched_at": datetime.now(UTC).isoformat(),
        },
    )
