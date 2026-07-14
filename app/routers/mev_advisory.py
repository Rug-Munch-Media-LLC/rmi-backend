"""MEV Protection Advisory - warns users before trading in sandwich-prone pools."""

import os

import httpx
from fastapi import APIRouter, Query

from app.core.internal_key import INTERNAL_API_KEY

router = APIRouter(prefix="/api/v1/mev-advisory", tags=["mev-advisory"])

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
RMI_KEY = INTERNAL_API_KEY


@router.get("/check/{address}")
async def check_mev_risk(address: str, chain: str = Query("ethereum")):
    """Check MEV risk before trading a token. Returns sandwich attack risk and protection recommendation."""  # noqa: E501
    async with httpx.AsyncClient(timeout=15) as c:
        # Use existing MEV sandwich detector
        r = await c.get(
            f"{BACKEND}/api/v1/databus/premium/mev/{address}?chain={chain}",
            headers={"X-RMI-Key": RMI_KEY},
        )

        mev_data = {}
        if r.status_code == 200:
            mev_data = r.json().get("data", r.json())

        sandwich_count = mev_data.get("sandwich_attacks_24h", 0)
        frontrun_count = mev_data.get("frontrun_count", 0)

        if sandwich_count > 10:
            risk = "CRITICAL"
            recommendation = "This pool has heavy MEV activity. Use Flashbots Protect or MEV Blocker for this trade."  # noqa: E501
            emoji = "🔴"
        elif sandwich_count > 3:
            risk = "HIGH"
            recommendation = (
                "Moderate sandwich risk detected. Consider MEV protection or lower slippage."
            )
            emoji = "🟡"
        elif sandwich_count > 0:
            risk = "LOW"
            recommendation = "Minor MEV activity. Standard slippage should be fine."
            emoji = "🟢"
        else:
            risk = "SAFE"
            recommendation = "No recent sandwich attacks detected. Safe to trade normally."
            emoji = "🟢"

    return {
        "token": address,
        "chain": chain,
        "mev_risk": risk,
        "emoji": emoji,
        "sandwich_attacks_24h": sandwich_count,
        "frontrun_attacks_24h": frontrun_count,
        "recommendation": recommendation,
        "mev_protection_tools": ["Flashbots Protect", "MEV Blocker", "Eden Network"]
        if risk in ("CRITICAL", "HIGH")
        else [],
    }


@router.get("/safe-swap")
async def safe_swap_params(
    address: str, chain: str = "ethereum", amount_usd: float = Query(100, ge=1)
):
    """Get optimal swap parameters to avoid MEV."""
    risk = await check_mev_risk(address, chain)

    if risk["mev_risk"] in ("CRITICAL", "HIGH"):
        slippage = 0.5
        use_flashbots = True
    else:
        slippage = 1.0
        use_flashbots = False

    return {
        "token": address,
        "chain": chain,
        "recommended_slippage_pct": slippage,
        "use_mev_protection": use_flashbots,
        "gas_tip_gwei": 2 if use_flashbots else 1,
        "mev_risk": risk["mev_risk"],
        "note": risk["recommendation"],
    }
