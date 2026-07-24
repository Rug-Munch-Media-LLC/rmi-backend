#!/usr/bin/env python3
"""#14 - Honeypot Network Map. Maps all known honeypot contracts, clusters by deployer,
fund flows. Interactive graph showing the honeypot factory network."""

import httpx
from fastapi import APIRouter, Query

from app.core.errors import UpstreamError
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/api/v1/honeypot-map", tags=["honeypot-map"])

BACKEND = settings.backend_url


class HoneypotNode(BaseModel):
    id: str
    label: str
    type: str = "contract"  # contract, deployer, funder, victim
    chain: str
    total_victims: int = 0
    total_stolen_usd: float = 0
    risk_score: int = 0


class HoneypotEdge(BaseModel):
    source: str
    target: str
    relationship: str  # deployed, funded_by, stole_from
    amount_usd: float = 0


@router.get("/deployer/{deployer_address}")
async def get_honeypot_deployer(deployer_address: str, chain: str = Query("ethereum")) -> dict:
    """Map all honeypot contracts deployed by a single address."""
    nodes: list[dict] = []
    edges: list[dict] = []

    # Deployer node
    deployer_id = deployer_address[:16]
    nodes.append(
        {
            "id": deployer_id,
            "label": f"Deployer {deployer_address[:10]}",
            "type": "deployer",
            "chain": chain,
        }
    )

    # Find all contracts deployed
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BACKEND}/api/v1/eth-labels/search",
                params={"address": deployer_address, "type": "deployer"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for entry in data.get("labels", []):
                    is_honeypot = any(
                        w in (entry.get("label", "") or "").lower()
                        for w in ["honeypot", "scam", "rug"]
                    )
                    contract_id = entry.get("contract_address", "")[:16]
                    if contract_id:
                        nodes.append(
                            {
                                "id": contract_id,
                                "label": entry.get("name", contract_id),
                                "type": "honeypot" if is_honeypot else "contract",
                                "chain": entry.get("chain", chain),
                                "risk_score": 80 if is_honeypot else 30,
                            }
                        )
                        edges.append(
                            {
                                "source": deployer_id,
                                "target": contract_id,
                                "relationship": "deployed",
                            }
                        )
    except (TypeError, ValueError, KeyError):
        pass

    return {
        "nodes": nodes,
        "edges": edges,
        "total_contracts": len([n for n in nodes if n["type"] != "deployer"]),
    }


@router.get("/network")
async def get_honeypot_network(
    chain: str = Query("ethereum"),
    limit: int = Query(50, le=100),
    min_risk: int = Query(50, le=100),
) -> dict:
    """Get the current honeypot network map for a chain. Clusters by deployer groups."""
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_deployers: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BACKEND}/api/v1/eth-labels/search",
                params={"q": "honeypot", "type": "deployer", "limit": limit},
            )
            if resp.status_code == 200:
                entries = resp.json().get("labels", [])
                for entry in entries[:limit]:
                    deployer = entry.get("address", "")
                    if deployer and deployer not in seen_deployers:
                        seen_deployers.add(deployer)
                        dep_id = deployer[:16]
                        nodes.append(
                            {
                                "id": dep_id,
                                "label": f"Deployer {deployer[:10]}",
                                "type": "deployer",
                                "chain": entry.get("chain", chain),
                                "risk_score": entry.get("risk_score", 70),
                            }
                        )

                        # Get their contracts
                        for contract in entry.get("contracts", [])[:5]:
                            c_id = contract.get("address", "")[:16]
                            if c_id:
                                nodes.append(
                                    {
                                        "id": c_id,
                                        "label": contract.get("name", c_id),
                                        "type": "contract",
                                        "chain": entry.get("chain", chain),
                                        "risk_score": 75,
                                    }
                                )
                                edges.append(
                                    {
                                        "source": dep_id,
                                        "target": c_id,
                                        "relationship": "deployed",
                                    }
                                )
    except Exception as e:
        raise UpstreamError(f"Labels service unavailable: {e}") from e

    return {
        "chain": chain,
        "nodes": nodes,
        "edges": edges,
        "total_deployers": len(seen_deployers),
        "total_contracts": len([n for n in nodes if n["type"] == "contract"]),
    }


@router.get("/stats")
async def get_honeypot_stats() -> dict[str, str]:
    """Global honeypot statistics."""
    return {
        "description": "Honeypot contract network intelligence from eth-labels + SENTINEL + Real-CATS",
        "data_sources": [
            "eth-labels (115K addresses)",
            "Real-CATS (153K tokens)",
            "SENTINEL scanner",
        ],
        "chains_supported": [
            "ethereum",
            "bsc",
            "polygon",
            "arbitrum",
            "base",
            "avalanche",
            "optimism",
        ],
    }
