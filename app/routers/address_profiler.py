#!/usr/bin/env python3
"""#2 - Cross-Chain Address Profiler. Input one address, get activity across all chains.
Shows portfolio, active chains, protocol usage, risk score. Uses DataBus + eth-labels."""

import asyncio
import os
from typing import Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/api/v1/address-profiler", tags=["address-profiler"])

CHAINS = {
    "ethereum": "1",
    "solana": "solana",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "polygon": "137",
    "avalanche": "43114",
    "optimism": "10",
    "fantom": "250",
    "linea": "59144",
}

DATABUS_BASE = os.environ.get("DATABUS_URL", f"{settings.backend_url}/api/v1/databus")
ETH_LABELS_URL = os.environ.get("ETH_LABELS_URL", f"{settings.backend_url}/api/v1/eth-labels")


class AddressProfile(BaseModel):
    address: str
    labels: list[dict[str, str]] = []
    chains_activity: list[dict[str, Any]] = []
    total_value_usd: float = 0
    risk_score: int = 50
    risk_factors: list[str] = []


async def _fetch_chain_balance(chain: str, chain_id: str, address: str) -> dict:
    """Fetch balance/activity for an address on one chain via DataBus."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{DATABUS_BASE}/{chain}/balance/{address}")
            if resp.status_code == 200:
                return {"chain": chain, **resp.json()}
    except (httpx.HTTPError, ConnectionError, TimeoutError):
        pass
    return {"chain": chain, "balance_usd": 0, "tx_count": 0, "error": "unavailable"}


async def _fetch_eth_labels(address: str) -> list[dict]:
    """Look up address in eth-labels database."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ETH_LABELS_URL}/lookup/{address}")
            if resp.status_code == 200:
                return resp.json().get("labels", [])
    except (httpx.HTTPError, ConnectionError, TimeoutError):
        pass
    return []


@router.get("/profile/{address}")
async def get_address_profile(address: str, chains: str = Query(None)) -> Any:
    """Get cross-chain profile for an address. Use ?chains=ethereum,solana to filter."""
    chain_list = chains.split(",") if chains else list(CHAINS.keys())

    # Fetch all chains in parallel
    tasks = []
    for c in chain_list:
        if c in CHAINS:
            tasks.append(_fetch_chain_balance(c, CHAINS[c], address))

    chain_results = await asyncio.gather(*tasks)

    # Fetch labels
    labels = await _fetch_eth_labels(address)

    # Compute profile
    chains_active = [
        r for r in chain_results if r.get("balance_usd", 0) > 0 or r.get("tx_count", 0) > 0
    ]
    total_value = sum(r.get("balance_usd", 0) or 0 for r in chains_active)

    # Risk scoring (simple heuristic)
    risk_factors: list[str] = []
    risk = 30  # lower = riskier
    for label in labels:
        lbl = label.get("label", "").lower()
        if any(w in lbl for w in ["scam", "phish", "hack", "exploit", "sanction", "mixer"]):
            risk_factors.append(f"Labeled: {label.get('label')}")
            risk = max(risk, 65)

    if len(chains_active) == 0:
        risk = max(risk, 50)
        risk_factors.append("No on-chain activity found")
    if len(chains_active) > 5:
        risk -= 10  # diverse activity = less risky

    profile = {
        "address": address,
        "labels": labels,
        "chains_active": len(chains_active),
        "chains_activity": chains_active,
        "total_value_usd": round(total_value, 2),
        "risk_score": min(100, max(0, risk)),
        "risk_factors": risk_factors,
    }
    return profile
