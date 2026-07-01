"""Billing module skeleton — x402 crypto payments ready, Stripe slot for later."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

# x402 pricing tiers (live)
TIERS = {
    "free": {"price_usd": 0, "requests_per_day": 100, "features": ["basic_scan", "market_data"]},
    "pro": {
        "price_usd": 19.99,
        "requests_per_day": 10000,
        "features": ["deep_scan", "signals", "whale_alerts", "api_access"],
    },
    "enterprise": {
        "price_usd": 499,
        "requests_per_day": 999999,
        "features": ["all", "custom_models", "white_label", "support"],
    },
}

# Stripe placeholder — ready when you add keys
STRIPE_ENABLED = False


class UpgradeRequest(BaseModel):
    user_id: str
    tier: str
    payment_method: str = "x402"  # x402 | stripe
    tx_signature: str | None = None


@router.get("/tiers")
async def get_tiers():
    return {"tiers": TIERS, "payment_methods": ["x402", "stripe (coming soon)"]}


@router.post("/upgrade")
async def upgrade_tier(req: UpgradeRequest):
    if req.tier not in TIERS:
        return {"error": "Invalid tier"}
    if req.payment_method == "stripe" and not STRIPE_ENABLED:
        return {"error": "Stripe not configured yet. Use x402 crypto payments."}
    return {"status": "upgraded", "user": req.user_id, "tier": req.tier, "payment": req.payment_method}


@router.get("/usage/{user_id}")
async def get_usage(user_id: str):
    return {"user_id": user_id, "tier": "free", "requests_today": 0, "limit": 100}
