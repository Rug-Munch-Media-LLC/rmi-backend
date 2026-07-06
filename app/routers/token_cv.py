#!/usr/bin/env python3
"""#5 - On-Chain CV for Tokens. One-page report: age, holders, liquidity, contract risks,
social signals, price history. Shareable permalink."""

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/token-cv", tags=["token-cv"])

BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")


class TokenCV(BaseModel):
    token_address: str
    chain: str
    symbol: str
    name: str
    age_days: int
    holders: int
    liquidity_usd: float
    volume_24h: float
    price_usd: float
    market_cap_usd: float
    safety_score: int
    risk_flags: list[str]
    security: dict[str, Any]
    social: dict[str, Any]
    deployer: dict[str, Any]
    summary: str


def _generate_summary(cv: dict) -> str:
    """Generate human-readable CV summary."""
    safety = cv.get("safety_score", 50)
    liq = cv.get("liquidity_usd", 0)
    age = cv.get("age_days", 0)

    if safety >= 70:
        verdict = "appears legitimate"
    elif safety >= 40:
        verdict = "has moderate risk factors"
    else:
        verdict = "shows HIGH-RISK characteristics"

    return (
        f"{cv.get('symbol', '?')} is a {age}-day-old token on {cv.get('chain', '?').upper()} "
        f"with ${liq:,.0f} liquidity and {cv.get('holders', 0)} holders. "
        f"SENTINEL rates it {safety}/100 - {verdict}."
    )


@router.get("/{chain}/{address}")
async def get_token_cv(chain: str, address: str):
    """Generate a complete on-chain CV for a token."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # SENTINEL scan
            resp = await client.post(
                f"{BACKEND}/api/v1/token/scan",
                json={"token_address": address, "chain": chain},
                headers={"X-RMI-Key": "rmi-internal-2026"},
            )
            if resp.status_code != 200:
                raise HTTPException(502, "Scanner unavailable")
            scan = resp.json()

    except Exception as e:
        raise HTTPException(502, f"Scan failed: {e}") from e

    free = scan.get("free", {})
    pro = scan.get("pro", {})

    cv = {
        "token_address": address,
        "chain": chain,
        "symbol": scan.get("symbol", "?"),
        "name": free.get("name", scan.get("symbol", "?")),
        "age_days": free.get("age_days", 0),
        "holders": free.get("holders", 0),
        "liquidity_usd": free.get("liquidity_usd", 0) or 0,
        "volume_24h": free.get("volume_24h", 0) or 0,
        "price_usd": free.get("price_usd", 0) or 0,
        "market_cap_usd": (free.get("price_usd", 0) or 0) * (free.get("supply", 0) or 0),
        "safety_score": scan.get("safety_score", 50),
        "risk_flags": scan.get("risk_flags", []),
        "security": {
            "honeypot_risk": free.get("honeypot_risk", "unknown"),
            "buy_tax": free.get("buy_tax", 0),
            "sell_tax": free.get("sell_tax", 0),
            "mint_authority": free.get("mint_authority", "unknown"),
            "freeze_authority": free.get("freeze_authority", "unknown"),
            "lp_burned": free.get("lp_burned", "unknown"),
            "contract_verified": free.get("contract_verified", False),
        },
        "deployer": {
            "address": pro.get("deployer_address", "unknown"),
            "tokens_created": pro.get("deployer_token_count", 0),
            "rug_rate": pro.get("deployer_rug_rate", 0),
        },
        "social": {
            "twitter": free.get("twitter", ""),
            "website": free.get("website", ""),
            "telegram": free.get("telegram", ""),
        },
    }
    cv["summary"] = _generate_summary(cv)
    return cv


@router.get("/compare")
async def compare_tokens(
    token_a: str = Query(...),
    chain_a: str = Query("solana"),
    token_b: str = Query(...),
    chain_b: str = Query("solana"),
):
    """Compare two tokens side by side."""
    async with httpx.AsyncClient(timeout=20) as client:

        async def fetch(addr, chain):
            try:
                resp = await client.post(
                    f"{BACKEND}/api/v1/token/scan",
                    json={"token_address": addr, "chain": chain},
                    headers={"X-RMI-Key": "rmi-internal-2026"},
                )
                if resp.status_code == 200:
                    return resp.json()
            except Exception:
                pass
            return {"error": "unavailable"}

        import asyncio

        a, b = await asyncio.gather(fetch(token_a, chain_a), fetch(token_b, chain_b))

    return {
        "token_a": {"address": token_a, "chain": chain_a, "data": a},
        "token_b": {"address": token_b, "chain": chain_b, "data": b},
    }
