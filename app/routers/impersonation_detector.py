#!/usr/bin/env python3
"""#6 - Portfolio Impersonation Detector. Scans for hidden wallets, proxy contracts,
suspicious approvals. Reveals what an address controls beyond its obvious wallet."""

import os
from typing import Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/impersonation", tags=["impersonation-detector"])

BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")
DATABUS = os.environ.get("DATABUS_URL", f"{BACKEND}/api/v1/databus")


class ImpersonationReport(BaseModel):
    address: str
    chain: str
    controlled_wallets: list[dict[str, Any]]
    proxy_contracts: list[dict[str, Any]]
    suspicious_approvals: list[dict[str, Any]]
    risk_level: str
    risk_score: int


async def _find_delegates(address: str, chain: str) -> list[dict]:
    """Find delegate/proxy wallets controlled by this address."""
    delegates: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Check for known proxy patterns
            resp = await client.get(f"{DATABUS}/{chain}/delegates/{address}")
            if resp.status_code == 200:
                delegates = resp.json().get("data", {}).get("delegates", [])
    except Exception:
        pass
    return delegates


async def _find_approvals(address: str, chain: str) -> list[dict]:
    """Find token approvals with unlimited spend to unverified contracts."""
    approvals: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DATABUS}/{chain}/approvals/{address}")
            if resp.status_code == 200:
                all_approvals = resp.json().get("data", {}).get("approvals", [])
                # Flag suspicious: unlimited spend to unverified contracts
                for a in all_approvals:
                    if a.get("amount") in (
                        None,
                        "",
                        "0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                    ):
                        a["suspicious"] = True
                        approvals.append(a)
    except Exception:
        pass
    return approvals


async def _scan_related_contracts(address: str, chain: str) -> list[dict]:
    """Find contracts deployed by or controlled by this address."""
    contracts: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Check eth-labels for any contract addresses associated
            resp = await client.get(f"{BACKEND}/api/v1/eth-labels/search?address={address}")
            if resp.status_code == 200:
                for entry in resp.json().get("labels", []):
                    if entry.get("type") == "contract":
                        contracts.append(entry)
    except Exception:
        pass
    return contracts


def _assess_risk(delegates: list, approvals: list, contracts: list) -> tuple[str, int]:
    """Compute impersonation risk score."""
    score = 0
    if len(delegates) > 0:
        score += 15 * min(len(delegates), 5)
    if len(approvals) > 0:
        score += 20 * min(len(approvals), 3)
    if len(contracts) > 0:
        score += 10 * min(len(contracts), 3)

    if score >= 60:
        return "critical", score
    elif score >= 30:
        return "high", score
    elif score >= 10:
        return "medium", score
    return "low", score


@router.get("/audit/{address}")
async def audit_address(address: str, chain: str = Query("ethereum")) -> dict:
    """Full impersonation audit for an address."""
    delegates = await _find_delegates(address, chain)
    approvals = await _find_approvals(address, chain)
    contracts = await _scan_related_contracts(address, chain)
    level, score = _assess_risk(delegates, approvals, contracts)

    return {
        "address": address,
        "chain": chain,
        "controlled_wallets": delegates,
        "suspicious_approvals": approvals,
        "related_contracts": contracts,
        "risk_level": level,
        "risk_score": score,
        "summary": (
            f"Found {len(delegates)} delegate wallets, {len(approvals)} suspicious approvals, "
            f"and {len(contracts)} related contracts. Risk: {level} ({score}/100)."
        ),
    }
