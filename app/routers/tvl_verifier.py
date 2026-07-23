#!/usr/bin/env python3
"""#16 - DeFiLlama Protocol Mismatch Detector. Compares DeFiLlama TVL against
actual on-chain balances. Flags inflated TVL or impending rug."""

import asyncio
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/v1/tvl-verify", tags=["tvl-verifier"])

DEFILLAMA_API = "https://api.llama.fi"
DATABUS = os.environ.get("DATABUS_URL", "http://localhost:8000/api/v1/databus")


async def _get_defillama_tvl(protocol_slug: str) -> dict[str, Any]:
    """Get current TVL from DeFiLlama."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DEFILLAMA_API}/protocol/{protocol_slug}")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "name": data.get("name", protocol_slug),
                    "slug": protocol_slug,
                    "tvl_usd": data.get("tvl", 0),
                    "chain_tvls": data.get("chainTvls", {}),
                    "tvl_change_24h": data.get("change_1d"),
                }
    except Exception:
        pass
    return {
        "name": protocol_slug,
        "slug": protocol_slug,
        "tvl_usd": 0,
        "chain_tvls": {},
        "error": "DeFiLlama unavailable",
    }


async def _get_onchain_tvl(protocol_name: str, chains: list[str]) -> dict[str, float]:
    """Get actual on-chain TVL from DataBus for given chains."""
    chain_tvls: dict[str, float] = {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            tasks = []
            for c in chains[:5]:
                tasks.append(client.get(f"{DATABUS}/{c}/tvl/{protocol_name.lower()}"))
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for i, resp in enumerate(responses):
                if not isinstance(resp, Exception) and resp.status_code == 200:
                    data = resp.json().get("data", {})
                    chain_tvls[chains[i]] = data.get("tvl_usd", 0) or 0
    except Exception:
        pass
    return chain_tvls


def _compute_discrepancy(defl_tvl: float, onchain_tvl: float) -> dict:
    """Compute TVL discrepancy with risk assessment."""
    if defl_tvl <= 0 or onchain_tvl <= 0:
        return {"discrepancy_pct": 0, "risk": "unknown", "explanation": "Insufficient data"}

    diff_pct = ((defl_tvl - onchain_tvl) / onchain_tvl) * 100

    if diff_pct > 30:
        risk = "CRITICAL"
        explanation = (
            f"DeFiLlama reports {diff_pct:.0f}% higher TVL than on-chain. Possible inflated TVL."
        )
    elif diff_pct > 15:
        risk = "HIGH"
        explanation = f"Significant discrepancy ({diff_pct:.0f}%). Verify manually."
    elif diff_pct < -30:
        risk = "HIGH"
        explanation = (
            f"On-chain TVL exceeds reported TVL by {abs(diff_pct):.0f}%. Possible unreported risk."
        )
    elif diff_pct < -10:
        risk = "MEDIUM"
        explanation = f"On-chain TVL higher than reported ({abs(diff_pct):.0f}%)."
    else:
        risk = "LOW"
        explanation = f"TVL aligned within {abs(diff_pct):.0f}%."

    return {"discrepancy_pct": round(diff_pct, 1), "risk": risk, "explanation": explanation}


@router.get("/protocol/{slug}")
async def verify_protocol_tvl(slug: str) -> dict[str, Any | None]:
    """Verify a DeFiLlama protocol's TVL against on-chain data."""
    defl = await _get_defillama_tvl(slug)

    # Get chain list from DeFiLlama
    chain_tvls = defl.get("chain_tvls", {})
    chains = list(chain_tvls.keys()) if isinstance(chain_tvls, dict) else []

    # Fetch on-chain data for top chains
    onchain = await _get_onchain_tvl(slug, chains[:5] if chains else ["ethereum"])
    total_onchain = sum(onchain.values())

    discrepancy = _compute_discrepancy(defl.get("tvl_usd", 0) or 0, total_onchain)

    return {
        "protocol": defl.get("name", slug),
        "slug": slug,
        "defillama_tvl": defl.get("tvl_usd", 0) or 0,
        "onchain_tvl": round(total_onchain, 2),
        "chains_verified": len(onchain),
        "discrepancy": discrepancy,
        "chain_breakdown": {
            "defillama": {c: chain_tvls.get(c, 0) for c in chains[:5]},
            "onchain": onchain,
        },
    }


@router.get("/top-mismatches")
async def find_top_mismatches(limit: int = Query(10, le=20)) -> dict:
    """Scan top DeFiLlama protocols for TVL mismatches."""
    # Fetch top protocols from DeFiLlama
    top_protocols: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DEFILLAMA_API}/protocols")
            if resp.status_code == 200:
                protocols = resp.json()
                top_protocols = [p["slug"] for p in protocols[: limit * 2] if p.get("slug")]
    except Exception:
        raise HTTPException(502, "DeFiLlama unavailable") from None

    mismatches: list[dict] = []
    for slug in top_protocols[: limit * 2]:
        result = await verify_protocol_tvl(slug)
        disc = result.get("discrepancy", {})
        if disc.get("risk") in ("CRITICAL", "HIGH"):
            mismatches.append(result)
        if len(mismatches) >= limit:
            break

    mismatches.sort(key=lambda m: abs(m["discrepancy"].get("discrepancy_pct", 0)), reverse=True)
    return {"mismatches": mismatches, "total_scanned": len(top_protocols[: limit * 2])}
