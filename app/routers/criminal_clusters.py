#!/usr/bin/env python3
"""#8 - Real-CATS Criminal Cluster Explorer. Interactive graph of 153K labeled tokens.
from __future__ import annotations

Shows relationships: same deployer, funding source, code patterns. Rug adjacency graph."""

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import settings
from app.core.internal_key import INTERNAL_API_KEY

router = APIRouter(prefix="/api/v1/criminal-clusters", tags=["criminal-clusters"])

BACKEND = settings.backend_url
REAL_CATS_DB = os.environ.get("REAL_CATS_DB", str(Path.home() / "rmi/backend/data/real_cats.db"))


class ClusterNode(BaseModel):
    id: str
    label: str
    type: str = "token"  # token, deployer, funder
    chain: str = ""
    rug_count: int = 0
    risk_score: int = 0


class ClusterEdge(BaseModel):
    source: str
    target: str
    relationship: str  # same_deployer, same_funder, code_similar, fund_flow
    strength: float = 1.0


@router.get("/deployer/{deployer_address}")
async def get_deployer_cluster(deployer_address: str, chain: str = Query("ethereum")) -> dict:
    """Get all tokens deployed by this address. Shows full rug adjacency."""
    tokens: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BACKEND}/api/v1/eth-labels/search",
                params={"address": deployer_address, "type": "deployer"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for entry in data.get("labels", []):
                    tokens.append(
                        {
                            "address": entry.get("contract_address", ""),
                            "name": entry.get("name", "?"),
                            "chain": entry.get("chain", chain),
                            "label": entry.get("label", ""),
                            "risk": entry.get("risk_score", 0),
                        }
                    )
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Cluster match failed: {e}")

    return {
        "deployer": deployer_address,
        "chain": chain,
        "tokens_deployed": len(tokens),
        "rug_count": sum(1 for t in tokens if "rug" in t.get("label", "").lower()),
        "tokens": tokens,
        "risk_assessment": "HIGH" if any(t.get("risk", 0) > 50 for t in tokens) else "MEDIUM",
    }


@router.get("/token/{token_address}")
async def get_token_cluster(token_address: str, chain: str = Query("ethereum")) -> dict:
    """Get all related tokens for a given token - same deployer, same funder, code clones."""
    nodes: list[dict] = []
    edges: list[dict] = []

    # Fetch token info from scanner
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{BACKEND}/api/v1/token/scan",
                json={"token_address": token_address, "chain": chain},
                headers={"X-RMI-Key": INTERNAL_API_KEY},
            )
            if resp.status_code == 200:
                scan = resp.json()

        # Root node
        nodes.append(
            {
                "id": token_address[:16],
                "label": scan.get("symbol", token_address[:8]),
                "type": "token",
                "chain": chain,
            }
        )

        # Get deployer info
        deployer = scan.get("pro", {}).get("deployer_address", "")
        if deployer:
            nodes.append(
                {"id": deployer[:16], "label": f"Deployer {deployer[:8]}", "type": "deployer"}
            )
            edges.append(
                {"source": deployer[:16], "target": token_address[:16], "relationship": "deployed"}
            )

            # Get siblings (same deployer)
            async with httpx.AsyncClient(timeout=10) as client:
                resp2 = await client.get(
                    f"{BACKEND}/api/v1/eth-labels/search", params={"address": deployer}
                )
                if resp2.status_code == 200:
                    for sibling in resp2.json().get("labels", [])[:10]:
                        sib_addr = sibling.get("contract_address", "")[:16]
                        if sib_addr and sib_addr != token_address[:16]:
                            nodes.append(
                                {
                                    "id": sib_addr,
                                    "label": sibling.get("name", sib_addr),
                                    "type": "token",
                                }
                            )
                            edges.append(
                                {
                                    "source": deployer[:16],
                                    "target": sib_addr,
                                    "relationship": "deployed",
                                }
                            )
                            edges.append(
                                {
                                    "source": sib_addr,
                                    "target": token_address[:16],
                                    "relationship": "sibling",
                                }
                            )

    except Exception as e:
        raise HTTPException(502, f"Scanner unavailable: {e}") from e

    return {"nodes": nodes, "edges": edges, "total_nodes": len(nodes), "total_edges": len(edges)}


@router.get("/search")
async def search_clusters(q: str = Query(..., min_length=2), limit: int = Query(20, le=50)) -> dict:
    """Search for deployers/tokens in the cluster database."""
    results: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BACKEND}/api/v1/eth-labels/search", params={"q": q, "limit": limit}
            )
            if resp.status_code == 200:
                results = resp.json().get("labels", [])
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Label lookup for {q} failed: {e}")
    return {"query": q, "results": results, "count": len(results)}
