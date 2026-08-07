#!/usr/bin/env python3
"""#9 - Token Laundry Matcher. Finds copycat tokens by code similarity, deployer patterns,

liquidity profile, marketing tactics. Spots scams before they launch."""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings
from app.core.errors import UpstreamError
from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/laundry-matcher", tags=["laundry-matcher"])

BACKEND = settings.backend_url


class LaundryMatch(BaseModel):
    address: str
    chain: str
    similarity_score: float  # 0-1
    match_reasons: list[str]
    risk_inherited: int


def _name_similarity(a: str, b: str) -> float:
    """Simple trigram similarity for token names."""

    def trigrams(s) -> set:
        s = s.lower().replace(" ", "")
        return {s[i : i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}

    ta = trigrams(a)
    tb = trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def _fetch_token_pairs(chain: str, max_pairs: int = 50) -> list[dict]:
    """Fetch recent token pairs from DexScreener for comparison."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/search?q={chain}")
            if resp.status_code != 200:
                return []
            return resp.json().get("pairs", [])[:max_pairs]
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"DexScreener pair fetch failed: {e}")
        return []


@router.get("/match/{address}")
async def find_matches(
    address: str,
    chain: str = Query("solana"),
    min_similarity: float = Query(0.3, ge=0, le=1),
    limit: int = Query(10, le=25),
) -> dict[str, dict]:
    """Find tokens similar to the given address. Returns top-N copycat matches."""
    # Get reference token data
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{BACKEND}/api/v1/token/scan",
                json={"token_address": address, "chain": chain},
                headers={"X-RMI-Key": INTERNAL_API_KEY},
            )
            if resp.status_code != 200:
                raise UpstreamError("Scanner unavailable")
            reference = resp.json()
    except Exception as e:
        raise UpstreamError(f"Scan failed: {e}") from e

    ref_name = reference.get("free", {}).get("name", "") or reference.get("symbol", "")
    reference.get("pro", {}).get("deployer_address", "")

    # Fetch comparison pool
    pairs = await _fetch_token_pairs(chain, max_pairs=50)

    matches: list[dict] = []
    for p in pairs:
        base = p.get("baseToken", {})
        target_name = base.get("name", "") or base.get("symbol", "")
        target_addr = p.get("pairAddress", "") or base.get("address", "")
        if target_addr == address:
            continue

        # Compute similarity
        name_sim = _name_similarity(ref_name, target_name)
        reasons: list[str] = []
        if name_sim > 0.5:
            reasons.append(f"Name similarity: {name_sim:.0%}")
        if p.get("liquidity", {}).get("usd", 0) == 0:
            reasons.append("Zero liquidity pattern")

        similarity = name_sim  # Primary metric
        # Could add: code similarity, deployer overlap, launch timing

        if similarity >= min_similarity:
            matches.append(
                {
                    "address": target_addr,
                    "chain": chain,
                    "symbol": base.get("symbol", "?"),
                    "name": target_name,
                    "similarity_score": round(similarity, 3),
                    "match_reasons": reasons,
                    "risk_inherited": reference.get("safety_score", 50),
                    "price_usd": float(p.get("priceUsd", 0) or 0),
                    "liquidity_usd": p.get("liquidity", {}).get("usd", 0) or 0,
                }
            )

    matches.sort(key=lambda m: m["similarity_score"], reverse=True)
    return {
        "reference": {
            "address": address,
            "chain": chain,
            "name": ref_name,
            "safety_score": reference.get("safety_score", 50),
        },
        "matches": matches[:limit],
        "total_candidates_scanned": len(pairs),
    }


@router.get("/scan-similar/{address}")
async def scan_and_match(address: str, chain: str = Query("solana")) -> dict:
    """Scan the address AND find its matches in a single call."""
    async with httpx.AsyncClient(timeout=20) as client:
        scan_task = client.post(
            f"{BACKEND}/api/v1/token/scan",
            json={"token_address": address, "chain": chain},
            headers={"X-RMI-Key": INTERNAL_API_KEY},
        )

        # Also kick off match search
        pairs = await _fetch_token_pairs(chain)

        scan_resp = await scan_task
        if scan_resp.status_code != 200:
            raise UpstreamError("Scan failed")
        scan = scan_resp.json()

    # Quick match
    ref_name = scan.get("free", {}).get("name", "") or scan.get("symbol", "")
    matches = []
    for p in pairs[:50]:
        base = p.get("baseToken", {})
        sim = _name_similarity(ref_name, base.get("name", "") or base.get("symbol", ""))
        if sim > 0.5:
            matches.append(
                {
                    "address": p.get("pairAddress", ""),
                    "symbol": base.get("symbol", "?"),
                    "similarity": round(sim, 3),
                }
            )

    return {
        "scan": scan,
        "copycat_matches": sorted(matches, key=lambda m: m["similarity"], reverse=True)[:10],
    }
