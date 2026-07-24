"""Stripe Integration - credit card payments for x402 API credits.

Users buy credits with a card → we credit their API key → they use x402 tools.
The x402 payment protocol runs on our backend. The user never touches crypto.

Endpoints:
  POST /api/v1/stripe/checkout    - Create Stripe checkout session
  POST /api/v1/stripe/webhook     - Stripe webhook (payment confirmations)
  GET  /api/v1/stripe/portal      - Customer portal (manage subscriptions)
  GET  /api/v1/credits/balance    - Check remaining credits
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.logging import get_logger

logger = get_logger("stripe")

# Stripe keys from environment
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

try:
    import stripe as _stripe_check  # noqa: F401

    STRIPE_PACKAGE_INSTALLED = True
except ImportError:
    STRIPE_PACKAGE_INSTALLED = False

STRIPE_ENABLED = bool(STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY and STRIPE_PACKAGE_INSTALLED)

# Credit packages (USD -> API credits)
CREDIT_PACKAGES = {
    "starter": {"price_usd": 1000, "credits": 1000, "label": "Starter Pack - 1,000 credits"},
    "growth": {
        "price_usd": 5000,
        "credits": 6000,
        "label": "Growth Pack - 6,000 credits (16% off)",
    },
    "pro": {"price_usd": 10000, "credits": 14000, "label": "Pro Pack - 14,000 credits (28% off)"},
    "enterprise": {
        "price_usd": 50000,
        "credits": 80000,
        "label": "Enterprise Pack - 80,000 credits (37% off)",
    },
}

# Monthly subscription tiers
SUBSCRIPTION_TIERS = {
    "starter_monthly": {"price_usd": 2900, "credits_per_month": 1000, "label": "Starter - $29/mo"},
    "pro_monthly": {"price_usd": 9900, "credits_per_month": 5000, "label": "Pro - $99/mo"},
    "team_monthly": {"price_usd": 29900, "credits_per_month": 25000, "label": "Team - $299/mo"},
}

router = APIRouter(prefix="/api/v1", tags=["Stripe"])

class CheckoutRequest(BaseModel):
    package: str = ""
    tier: str = ""
    success_url: str = ""
    cancel_url: str = ""
    api_key: str = ""
    email: str = ""

class CreditEntry:
    """In-memory credit store - replace with Redis/Postgres in production."""

    def __init__(self) -> None:
        self._credits: dict[str, dict] = {}

    def get_balance(self, api_key: str) -> int:
        entry = self._credits.get(api_key, {"balance": 100})  # 100 free credits
        return entry.get("balance", 0)

    def add_credits(self, api_key: str, amount: int, source: str = "stripe") -> None:
        if api_key not in self._credits:
            self._credits[api_key] = {
                "balance": 100,
                "total_purchased": 0,
                "created_at": time.time(),
            }
        self._credits[api_key]["balance"] += amount
        self._credits[api_key]["total_purchased"] += amount
        self._credits[api_key]["last_purchase"] = time.time()
        logger.info(f"Credits added: {amount} to {api_key[:16]} via {source}")

    def deduct(self, api_key: str, amount: int = 1) -> bool:
        entry = self._credits.get(api_key)
        if not entry or entry["balance"] < amount:
            return False
        entry["balance"] -= amount
        return True

    def spending(self, api_key: str) -> dict:
        entry = self._credits.get(api_key, {"balance": 100, "total_purchased": 0})
        return {
            "balance": entry["balance"],
            "total_purchased": entry["total_purchased"],
        }

_credit_store = CreditEntry()

@router.post("/stripe/checkout")
async def create_checkout(req: CheckoutRequest) -> Any | dict:
    """Create a Stripe checkout session for credit purchase."""
    if not STRIPE_ENABLED:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Stripe not configured",
                "note": "Add STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY to environment. Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY in environment",
                "packages": {
                    k: {"price_usd": v["price_usd"], "credits": v["credits"], "label": v["label"]}
                    for k, v in CREDIT_PACKAGES.items()
                },
                "subscriptions": {
                    k: {
                        "price_usd": v["price_usd"],
                        "credits_per_month": v["credits_per_month"],
                        "label": v["label"],
                    }
                    for k, v in SUBSCRIPTION_TIERS.items()
                },
            },
        )

    try:
        import stripe

        stripe.api_key = STRIPE_SECRET_KEY

        package = None
        if req.package and req.package in CREDIT_PACKAGES:
            package = CREDIT_PACKAGES[req.package]
        elif req.tier and req.tier in SUBSCRIPTION_TIERS:
            package = SUBSCRIPTION_TIERS[req.tier]

        if not package:
            packages_list = list(CREDIT_PACKAGES.keys()) + list(SUBSCRIPTION_TIERS.keys())
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid package or tier", "available": packages_list},
            )

        is_subscription = req.tier in SUBSCRIPTION_TIERS

        checkout_session = stripe.checkout.Session.create(
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": package["label"],
                            "description": f"{package.get('credits', package.get('credits_per_month', 0))} API credits for RugMunch Intelligence",
                        },
                        "unit_amount": package["price_usd"],
                        "recurring": {"interval": "month"} if is_subscription else None,
                    },
                    "quantity": 1,
                }
            ],
            mode="subscription" if is_subscription else "payment",
            success_url=req.success_url or "https://rugmunch.io/credits?success=true",
            cancel_url=req.cancel_url or "https://rugmunch.io/credits?canceled=true",
            metadata={"api_key": req.api_key, "package": req.package or req.tier},
            customer_email=req.email or None,
        )

        return {
            "session_id": checkout_session.id,
            "checkout_url": checkout_session.url,
            "publishable_key": STRIPE_PUBLISHABLE_KEY,
        }

    except Exception as e:
        logger.error(f"Stripe checkout error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)[:200]})

@router.post("/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, bool] | Any:
    """Stripe webhook - called when payment succeeds."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not STRIPE_ENABLED:
        logger.warning("Stripe webhook received but Stripe not configured")
        return {"received": True, "processed": False}

    try:
        import stripe

        stripe.api_key = STRIPE_SECRET_KEY

        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)

        if event.get("type") == "checkout.session.completed":
            session = event["data"]["object"]
            api_key = session.get("metadata", {}).get("api_key", "")
            package_name = session.get("metadata", {}).get("package", "")

            if package_name in CREDIT_PACKAGES:
                credits = CREDIT_PACKAGES[package_name]["credits"]
            elif package_name in SUBSCRIPTION_TIERS:
                credits = SUBSCRIPTION_TIERS[package_name]["credits_per_month"]
            else:
                credits = 0

            if api_key and credits > 0:
                _credit_store.add_credits(api_key, credits)
                logger.info(
                    f"Stripe payment: {session.get('id', '')} -> {credits} credits for {api_key[:16]}"
                )

        return {"received": True, "processed": True}

    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        return JSONResponse(status_code=400, content={"error": str(e)[:200]})

@router.get("/stripe/packages")
async def list_packages() -> dict[str, dict]:
    """List available credit packages and subscription tiers."""
    return {
        "packages": {
            k: {"price_usd": v["price_usd"], "credits": v["credits"], "label": v["label"]}
            for k, v in CREDIT_PACKAGES.items()
        },
        "subscriptions": {
            k: {
                "price_usd": v["price_usd"],
                "credits_per_month": v["credits_per_month"],
                "label": v["label"],
            }
            for k, v in SUBSCRIPTION_TIERS.items()
        },
        "stripe_configured": STRIPE_ENABLED,
        "publishable_key": STRIPE_PUBLISHABLE_KEY or "",
        "note": "Pay with credit card. No crypto wallet needed. Credits never expire.",
    }

@router.get("/credits/balance")
async def check_balance(api_key: str = "") -> dict[str, int | float] | dict[str, str]:
    """Check remaining credit balance for an API key."""
    if not api_key:
        return {"error": "api_key parameter required"}
    spending = _credit_store.spending(api_key)
    return {
        "api_key": api_key[:16] + "...",
        "balance": spending["balance"],
        "total_purchased": spending["total_purchased"],
        "free_tier": spending["total_purchased"] == 0,
    }

@router.post("/credits/buy")
async def buy_credits(api_key: str = "", amount_usd: int = 1000) -> dict:
    """Buy credits directly via API (wraps Stripe checkout).

    In production, this creates a Stripe checkout session.
    For testing, credits are added directly when STRIPE_ENABLED is False.
    """
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key required")

    if STRIPE_ENABLED:
        import stripe

        stripe.api_key = STRIPE_SECRET_KEY
        session = stripe.checkout.Session.create(
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": "API Credits",
                            "description": f"{amount_usd} credits",
                        },
                        "unit_amount": amount_usd,
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            metadata={"api_key": api_key, "manual_amount": str(amount_usd)},
        )
        return {"checkout_url": session.url, "session_id": session.id}

    # Sandbox mode: credit directly
    _credit_store.add_credits(api_key, amount_usd)
    balance = _credit_store.get_balance(api_key)
    return {
        "credits_added": amount_usd,
        "total_balance": balance,
        "price_per_call": "$0.01",
        "mode": "sandbox",
        "note": "Stripe not configured - credits added in sandbox mode. Add STRIPE_SECRET_KEY for live payments.",
    }

@router.get("/stripe/portal")
async def customer_portal(customer_id: str = "") -> dict | Any:
    """Stripe customer portal for managing subscriptions."""
    if not STRIPE_ENABLED or not customer_id:
        return {
            "available": STRIPE_ENABLED,
            "note": "Manage your subscription at rugmunch.io/account",
        }
    try:
        import stripe

        stripe.api_key = STRIPE_SECRET_KEY
        session = stripe.billing_portal.Session.create(customer=customer_id)
        return {"portal_url": session.url}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)[:200]})
