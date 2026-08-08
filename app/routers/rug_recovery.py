#!/usr/bin/env python3
"""#18 - Rug Recovery Indexer. When a rug is detected by SENTINEL, tracks all outbound

txs from deployer. Builds trace graph of stolen funds. Premium: notify affected addresses."""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings
from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/rug-recovery", tags=["rug-recovery"])

BACKEND = settings.backend_url
DATABUS = settings.databus_url


class RugTraceResult(BaseModel):
    rug_token: str
    chain: str
    deployer: str
    total_stolen_usd: float
    fund_hops: int
    destination_addresses: list[dict[str, Any]]
    cex_deposits: list[dict[str, Any]]
    mixer_usage: list[dict[str, Any]]
    recovery_possible: bool
    trace_graph: dict[str, Any]


async def _trace_outbound_txs(address: str, chain: str, max_hops: int = 3) -> list[dict[str, Any]]:
    """Trace outbound transactions from an address across hops."""
    txs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{DATABUS}/{chain}/transactions/{address}",
                params={"direction": "out", "limit": 50},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                txs = data.get("transactions", [])[:50]
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Outbound txs failed for {address}: {e}")
    return txs


async def _check_cex_deposit(address: str, chain: str) -> dict[str, Any] | None:
    """Check if an address is a known CEX deposit address."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{BACKEND}/api/v1/eth-labels/lookup/{address}")
            if resp.status_code == 200:
                for label in resp.json().get("labels", []):
                    lbl = (label.get("label", "") or "").lower()
                    if any(
                        w in lbl
                        for w in [
                            "exchange",
                            "cex",
                            "binance",
                            "coinbase",
                            "kraken",
                            "okx",
                            "bybit",
                        ]
                    ):
                        return {"address": address, "cex": label.get("label"), "chain": chain}
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"CEX deposit check failed for {address}: {e}")
    return None


async def _check_mixer(address: str, chain: str) -> dict[str, Any] | None:
    """Check if an address is a known mixer/tumbler."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{BACKEND}/api/v1/eth-labels/lookup/{address}")
            if resp.status_code == 200:
                for label in resp.json().get("labels", []):
                    lbl = (label.get("label", "") or "").lower()
                    if any(w in lbl for w in ["mixer", "tornado", "tumbler"]):
                        return {"address": address, "mixer": label.get("label"), "chain": chain}
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Mixer check failed for {address}: {e}")
    return None


@router.get("/trace/{token_address}")
async def trace_rug(
    token_address: str, chain: str = Query("ethereum"), max_hops: int = Query(3, le=5)
) -> dict[str, Any] | dict[str, str]:
    """Trace stolen funds from a rugged token."""
    # Step 1: Get deployer from SENTINEL
    deployer = ""
    total_stolen = 0.0
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{BACKEND}/api/v1/token/scan",
                json={"token_address": token_address, "chain": chain},
                headers={"X-RMI-Key": INTERNAL_API_KEY},
            )
            if resp.status_code == 200:
                scan = resp.json()
                deployer = scan.get("pro", {}).get("deployer_address", "")
                total_stolen = scan.get("free", {}).get("liquidity_usd", 0) or 0
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Deployer lookup failed for {token_address}: {e}")

    if not deployer:
        return {
            "error": "Could not identify deployer",
            "token_address": token_address,
            "chain": chain,
        }

    # Step 2: Trace outbound txs from deployer
    direct_txs = await _trace_outbound_txs(deployer, chain)

    # Step 3: Check destinations for CEX/mixer involvement
    destinations = set()
    for tx in direct_txs:
        to_addr = tx.get("to", "")
        if to_addr:
            destinations.add(to_addr)

    cex_hits: list[dict[str, Any]] = []
    mixer_hits: list[dict[str, Any]] = []
    for addr in list(destinations)[:20]:
        cex = await _check_cex_deposit(addr, chain)
        if cex:
            cex_hits.append(cex)
        mixer = await _check_mixer(addr, chain)
        if mixer:
            mixer_hits.append(mixer)

    recovery_possible = len(cex_hits) > 0

    # Build trace graph
    nodes = [{"id": deployer[:16], "label": f"Deployer {deployer[:10]}", "type": "deployer"}]
    edges: list[dict[str, Any]] = []
    for tx in direct_txs[:20]:
        to_id = tx.get("to", "")[:16]
        if to_id:
            nodes.append({"id": to_id, "label": f"Wallet {to_id}", "type": "wallet"})
            edges.append(
                {
                    "source": deployer[:16],
                    "target": to_id,
                    "amount_usd": float(tx.get("value_usd", 0) or 0),
                    "tx_hash": tx.get("hash", "?")[:16],
                }
            )

    return {
        "rug_token": token_address,
        "chain": chain,
        "deployer": deployer,
        "total_stolen_usd": round(total_stolen, 2),
        "fund_hops": 1,
        "destination_addresses": [
            {"address": a, "tx_count": sum(1 for tx in direct_txs if tx.get("to") == a)}
            for a in list(destinations)[:10]
        ],
        "cex_deposits": cex_hits,
        "mixer_usage": mixer_hits,
        "recovery_possible": recovery_possible,
        "trace_graph": {"nodes": nodes, "edges": edges},
        "recommendation": (
            "Funds traced to known CEX addresses. Contact exchange with tx hashes and police report."
            if recovery_possible
            else "No CEX deposits detected yet. Monitor deployer wallet for future CEX transfers."
        ),
    }


@router.get("/victims/{token_address}")
async def get_affected_victims(token_address: str, chain: str = Query("ethereum")) -> dict[str, Any]:
    """Get list of addresses that interacted with (lost money to) a rug token."""
    victims: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DATABUS}/{chain}/token/{token_address}/holders")
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                holders = data.get("holders", [])
                for h in holders[:50]:
                    if float(h.get("pnl_usd", 0) or 0) < -1:
                        victims.append(
                            {
                                "address": h.get("address", "")[:20] + "...",
                                "loss_usd": round(abs(float(h.get("pnl_usd", 0) or 0)), 2),
                                "tokens_held": h.get("balance", 0),
                            }
                        )
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Victims lookup failed for {token_address}: {e}")

    victims.sort(key=lambda v: v["loss_usd"], reverse=True)
    return {
        "token_address": token_address,
        "chain": chain,
        "victim_count": len(victims),
        "total_loss_usd": round(sum(v["loss_usd"] for v in victims), 2),
        "victims": victims[:20],
    }
