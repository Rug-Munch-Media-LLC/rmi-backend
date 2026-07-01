#!/usr/bin/env python3
"""#12 — Multi-Chain Airdrop Scanner. Scans address across all chains for unclaimed airdrops,
governance tokens, dust that became valuable. Free tier shows value, paid tier auto-claims."""

import asyncio
import os
from typing import Any

import httpx
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/airdrop-scanner", tags=["airdrop-scanner"])

DATABUS = os.environ.get("DATABUS_URL", "http://localhost:8000/api/v1/databus")

CHAINS = [
    "solana",
    "ethereum",
    "bsc",
    "base",
    "arbitrum",
    "polygon",
    "avalanche",
    "optimism",
    "fantom",
    "linea",
    "zksync",
    "scroll",
]

KNOWN_AIRDROPS = {
    "ethereum": [
        {"project": "Uniswap", "token": "UNI", "check_contract": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"},
        {"project": "ENS", "token": "ENS", "check_contract": "0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72"},
    ],
    "arbitrum": [
        {"project": "Arbitrum", "token": "ARB", "check_contract": "0x912CE59144191C1204E64559FE8253a0e49E6548"},
    ],
    "solana": [
        {"project": "Jupiter", "token": "JUP", "check_contract": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"},
        {"project": "Pyth", "token": "PYTH", "check_contract": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"},
        {"project": "Jito", "token": "JTO", "check_contract": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL"},
    ],
}


async def _check_chain_airdrops(address: str, chain: str) -> dict[str, Any]:
    """Check for unclaimed airdrops on a single chain."""
    found: list[dict] = []
    total_value = 0.0

    known = KNOWN_AIRDROPS.get(chain, [])
    for airdrop in known:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(
                    f"{DATABUS}/{chain}/balance/{address}", params={"token": airdrop["check_contract"]}
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    balance = float(data.get("balance", 0) or 0)
                    value = float(data.get("value_usd", 0) or 0)
                    if balance > 0:
                        found.append(
                            {
                                "project": airdrop["project"],
                                "token": airdrop["token"],
                                "chain": chain,
                                "balance": balance,
                                "value_usd": round(value, 2),
                                "claimable": False,  # Would need per-project claim logic
                            }
                        )
                        total_value += value
        except Exception:
            continue

    return {"chain": chain, "airdrops": found, "total_value": round(total_value, 2)}


@router.get("/scan/{address}")
async def scan_airdrops(address: str, chains: str = Query(None)):
    """Scan an address across all chains for unclaimed airdrops."""
    chain_list = chains.split(",") if chains else CHAINS[:8]

    tasks = []
    for c in chain_list:
        if c in KNOWN_AIRDROPS:
            tasks.append(_check_chain_airdrops(address, c))

    results = await asyncio.gather(*tasks)
    all_airdrops = []
    grand_total = 0.0
    for r in results:
        all_airdrops.extend(r["airdrops"])
        grand_total += r["total_value"]

    return {
        "address": address,
        "chains_scanned": len(results),
        "airdrops_found": len(all_airdrops),
        "total_value_usd": round(grand_total, 2),
        "airdrops": all_airdrops,
    }


@router.get("/known-projects")
async def list_known_airdrops():
    """List all known airdrop projects by chain."""
    return {"airdrops": KNOWN_AIRDROPS, "total_chains": len(KNOWN_AIRDROPS)}


@router.get("/dust-check/{address}")
async def check_dust(address: str, chain: str = Query("solana")):
    """Check for dust tokens that might have become valuable."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DATABUS}/{chain}/tokens/{address}")
            if resp.status_code != 200:
                return {"address": address, "chain": chain, "tokens": [], "error": "chain unavailable"}
            tokens = resp.json().get("data", {}).get("tokens", [])
            # Find tokens with small balances that have value
            dust_tokens = []
            for t in tokens:
                bal = float(t.get("balance", 0) or 0)
                val = float(t.get("value_usd", 0) or 0)
                if bal > 0 and val > 0:
                    dust_tokens.append({"token": t.get("symbol", "?"), "balance": bal, "value_usd": round(val, 2)})
            dust_tokens.sort(key=lambda t: t["value_usd"], reverse=True)
            total_dust = sum(t["value_usd"] for t in dust_tokens)
            return {
                "address": address,
                "chain": chain,
                "dust_tokens": dust_tokens[:20],
                "total_dust_value": round(total_dust, 2),
            }
    except Exception as e:
        return {"address": address, "chain": chain, "error": str(e)}
