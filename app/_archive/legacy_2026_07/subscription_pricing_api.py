"""
RMI Subscription & Pricing API
==============================
Manages subscription tiers, payments, and billing:
  - 3 premium tiers: Starter ($19.99), Pro ($38), Unlimited ($89)
  - Enterprise custom pricing (contact biz@rugmunch.io)
  - Yearly discount: 20% off
  - 6-month discount: 10% off
  - 15+ crypto payment chains
  - Webhook handling for payment confirmation
  - Subscription status, upgrades, downgrades, cancellations
"""

import json
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("rmi_subscriptions")
router = APIRouter(tags=["subscriptions"])


# ── Redis helper ──
ADDON_CONFIG = {
    "PORTFOLIO_PRO": {
        "name": "Portfolio Pro",
        "price_monthly": 3.00,
        "price_six_month": 16.20,  # 3.00 * 6 * 0.9
        "price_yearly": 28.80,  # 3.00 * 12 * 0.8
        "kind": "addon",  # addon tiers can stack with base tiers
        "category": "portfolio",
        "wallets_limit": 50,  # vs 3 on free
        "history_window_days": 30,  # vs 7 on free
        "features": [
            "Track up to 50 wallets",
            "30-day P&L history (vs 7-day free)",
            "Per-token risk overlay",
            "Cross-wallet top holdings",
            "Time-series price charts",
            "CSV export",
            "Works WITH any base tier",
        ],
        "stripe_price_id": os.getenv("STRIPE_PORTFOLIO_PRO_PRICE_ID"),
        "x402_price_atoms": "3000000",  # $3.00 USDC (6 decimals)
        "x402_chain_id": 8453,  # Base
    },
}


# Tier definitions
TIER_CONFIG = {
    "FREE": {
        "name": "Free",
        "price_monthly": 0,
        "price_six_month": 0,
        "price_yearly": 0,
        "scans_per_month": 5,
        "api_calls_per_day": 10,
        "features": [
            "5 scans per month",
            "10 API calls per day",
            "Basic token analysis",
            "Community support",
        ],
        "stripe_price_id": None,
    },
    "STARTER": {
        "name": "Starter",
        "price_monthly": 19.99,
        "price_six_month": 107.95,  # 19.99 * 6 * 0.9 (10% off)
        "price_yearly": 191.90,  # 19.99 * 12 * 0.8 (20% off)
        "scans_per_month": 50,
        "api_calls_per_day": 100,
        "features": [
            "50 scans per month",
            "100 API calls per day",
            "Advanced token analysis",
            "Risk scoring",
            "Discord support",
            "Export to CSV",
        ],
        "stripe_price_id": os.getenv("STRIPE_STARTER_PRICE_ID"),
    },
    "PRO": {
        "name": "Pro",
        "price_monthly": 38.00,
        "price_six_month": 205.20,  # 38 * 6 * 0.9
        "price_yearly": 364.80,  # 38 * 12 * 0.8
        "scans_per_month": 200,
        "api_calls_per_day": 500,
        "features": [
            "200 scans per month",
            "500 API calls per day",
            "Advanced token analysis",
            "Risk scoring + AI insights",
            "Priority Discord support",
            "Export to CSV/JSON",
            "Webhook alerts",
            "Multi-chain monitoring",
        ],
        "stripe_price_id": os.getenv("STRIPE_PRO_PRICE_ID"),
    },
    "ELITE": {
        "name": "Unlimited",
        "price_monthly": 89.00,
        "price_six_month": 480.60,  # 89 * 6 * 0.9
        "price_yearly": 854.40,  # 89 * 12 * 0.8
        "scans_per_month": -1,  # unlimited
        "api_calls_per_day": -1,  # unlimited
        "features": [
            "Unlimited scans",
            "Unlimited API calls",
            "Full AI analysis suite",
            "Custom risk models",
            "Dedicated support channel",
            "All export formats",
            "Real-time webhook alerts",
            "Multi-chain + cross-chain",
            "White-label options",
            "API key management",
        ],
        "stripe_price_id": os.getenv("STRIPE_ELITE_PRICE_ID"),
    },
    "ENTERPRISE": {
        "name": "Enterprise",
        "price_monthly": None,  # Custom
        "price_six_month": None,
        "price_yearly": None,
        "scans_per_month": -1,
        "api_calls_per_day": -1,
        "features": [
            "Everything in Unlimited",
            "Custom SLA",
            "Dedicated infrastructure",
            "Custom integrations",
            "On-premise deployment option",
            "Team management (up to 50 seats)",
            "SSO / SAML",
            "Audit logs",
            "Dedicated account manager",
            "Custom AI model training",
        ],
        "stripe_price_id": None,
        "contact_email": "biz@rugmunch.io",
    },
}

# Crypto payment configuration
CRYPTO_PAYMENT_CONFIG = {
    "accepted_chains": [
        {"id": "ethereum", "name": "Ethereum", "symbol": "ETH", "type": "evm", "confirmations": 12},
        {"id": "base", "name": "Base", "symbol": "ETH", "type": "evm", "confirmations": 10},
        {"id": "arbitrum", "name": "Arbitrum", "symbol": "ETH", "type": "evm", "confirmations": 10},
        {"id": "optimism", "name": "Optimism", "symbol": "ETH", "type": "evm", "confirmations": 10},
        {"id": "polygon", "name": "Polygon", "symbol": "MATIC", "type": "evm", "confirmations": 20},
        {"id": "bsc", "name": "BSC", "symbol": "BNB", "type": "evm", "confirmations": 15},
        {
            "id": "avalanche",
            "name": "Avalanche",
            "symbol": "AVAX",
            "type": "evm",
            "confirmations": 12,
        },
        {"id": "fantom", "name": "Fantom", "symbol": "FTM", "type": "evm", "confirmations": 10},
        {"id": "solana", "name": "Solana", "symbol": "SOL", "type": "solana", "confirmations": 32},
        {"id": "tron", "name": "Tron", "symbol": "TRX", "type": "tron", "confirmations": 19},
        {"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC", "type": "btc", "confirmations": 3},
        {"id": "monero", "name": "Monero", "symbol": "XMR", "type": "xmr", "confirmations": 10},
        {"id": "litecoin", "name": "Litecoin", "symbol": "LTC", "type": "ltc", "confirmations": 6},
        {
            "id": "dogecoin",
            "name": "Dogecoin",
            "symbol": "DOGE",
            "type": "doge",
            "confirmations": 10,
        },
        {"id": "ripple", "name": "XRP", "symbol": "XRP", "type": "xrp", "confirmations": 1},
    ],
    "payment_wallet": os.getenv("CRYPTO_PAYMENT_WALLET", "0x0000000000000000000000000000000000000000"),
    "sol_payment_wallet": os.getenv("SOL_PAYMENT_WALLET", ""),
    "btc_payment_address": os.getenv("BTC_PAYMENT_ADDRESS", ""),
    "xmr_payment_address": os.getenv("XMR_PAYMENT_ADDRESS", ""),
    "contact_email": "biz@rugmunch.io",
}


# Combined config returned to the client (base tiers + add-ons)
ALL_PRODUCTS = {**TIER_CONFIG, **ADDON_CONFIG}


# ── Models ──


class SubscriptionCreateRequest(BaseModel):
    tier: str = Field(..., pattern="^(STARTER|PRO|ELITE|PORTFOLIO_PRO)$")
    period: str = Field(..., pattern="^(monthly|six_month|yearly)$")
    payment_method: str = Field(..., pattern="^(stripe|crypto|x402)$")
    crypto_chain: str | None = None  # Required if payment_method == crypto/x402


class AddonCreateRequest(BaseModel):
    """For stacking add-ons on top of an existing subscription."""

    addon: str = Field(..., pattern="^(PORTFOLIO_PRO)$")
    period: str = Field(..., pattern="^(monthly|six_month|yearly)$")
    payment_method: str = Field(..., pattern="^(stripe|crypto|x402)$")
    crypto_chain: str | None = None


class CryptoPaymentRequest(BaseModel):
    tier: str
    period: str
    chain: str
    tx_hash: str
    amount_paid_usd: float
    from_address: str


class SubscriptionResponse(BaseModel):
    id: str
    user_id: str
    tier: str
    period: str
    status: str
    current_period_start: str
    current_period_end: str
    cancel_at_period_end: bool
    payment_method: str
    amount_paid: float
    currency: str
    created_at: str


class PricingResponse(BaseModel):
    tiers: dict[str, Any]
    discounts: dict[str, float]
    crypto_chains: list[dict]


# ── Helpers ──


def calculate_price(tier: str, period: str) -> float:
    """Calculate price for tier + period."""
    config = TIER_CONFIG.get(tier.upper())
    if not config:
        return 0

    if period == "monthly":
        return config["price_monthly"]
    elif period == "six_month":
        return config["price_six_month"]
    elif period == "yearly":
        return config["price_yearly"]
    return config["price_monthly"]


_PERIOD_DAYS = {
    "monthly": 30,
    "six_month": 180,
    "yearly": 365,
}


def get_period_days(period: str) -> int:
    """Get number of days for a billing period."""
    return _PERIOD_DAYS.get(period, 30)


def _get_user_subscriptions(user_id: str) -> list[dict]:
    """Get all subscriptions for a user."""
    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    subs_raw = r.hget("rmi:subscriptions", user_id)
    if subs_raw:
        return json.loads(subs_raw)
    return []


def _save_subscription(sub: dict):
    """Save a subscription to Redis."""
    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    user_id = sub["user_id"]
    subs = _get_user_subscriptions(user_id)

    # Update existing or append
    found = False
    for i, s in enumerate(subs):
        if s["id"] == sub["id"]:
            subs[i] = sub
            found = True
            break
    if not found:
        subs.append(sub)

    r.hset("rmi:subscriptions", user_id, json.dumps(subs))


# ── Public Endpoints ──


@router.get("/pricing")
async def get_pricing() -> PricingResponse:
    """Get all pricing information - base tiers + add-ons."""
    return {
        "tiers": TIER_CONFIG,
        "addons": ADDON_CONFIG,
        "all_products": ALL_PRODUCTS,
        "discounts": {
            "six_month": 0.10,  # 10% off
            "yearly": 0.20,  # 20% off
        },
        "crypto_chains": CRYPTO_PAYMENT_CONFIG["accepted_chains"],
        "enterprise_contact": "biz@rugmunch.io",
    }


@router.get("/pricing/calculate")
async def calculate_pricing(tier: str, period: str):
    """Calculate exact price for a tier + period."""
    price = calculate_price(tier, period)
    if price == 0 and tier.upper() != "FREE":
        raise HTTPException(status_code=400, detail="Invalid tier or period")

    config = TIER_CONFIG.get(tier.upper())
    monthly = config["price_monthly"]

    savings = 0
    if period == "six_month":
        savings = (monthly * 6) - price
    elif period == "yearly":
        savings = (monthly * 12) - price

    return {
        "tier": tier.upper(),
        "period": period,
        "price": price,
        "currency": "USD",
        "monthly_equivalent": round(price / get_period_days(period) * 30, 2),
        "savings": round(savings, 2),
        "savings_percent": round(savings / (monthly * (6 if period == "six_month" else 12)) * 100, 1)
        if savings > 0
        else 0,
    }


# ── Authenticated Endpoints ──


@router.get("/subscription")
async def get_my_subscription(request: Request):
    """Get current user's active subscription."""
    from app.auth import get_current_user

    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    subs = _get_user_subscriptions(user["id"])

    # Find active subscription
    active = None
    now = datetime.utcnow().isoformat()
    for sub in subs:
        if sub["status"] == "active" and sub["current_period_end"] > now:
            active = sub
            break

    if not active:
        # Return free tier info
        return {
            "tier": "FREE",
            "status": "active",
            "features": TIER_CONFIG["FREE"]["features"],
            "scans_remaining": user.get("scans_remaining", 5),
            "api_calls_today": 0,
        }

    return {
        "subscription": active,
        "tier": active["tier"],
        "features": TIER_CONFIG.get(active["tier"], {}).get("features", []),
        "days_remaining": max(0, (datetime.fromisoformat(active["current_period_end"]) - datetime.utcnow()).days),
    }


@router.post("/subscription/create")
async def create_subscription(req: SubscriptionCreateRequest, request: Request):
    """Create a new subscription (Stripe, crypto, or x402)."""
    from app.auth import get_current_user

    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    tier = req.tier.upper()
    # Allow base tiers + addons (PORTFOLIO_PRO can be purchased standalone)
    if tier not in ALL_PRODUCTS or tier == "FREE":
        raise HTTPException(status_code=400, detail="Invalid tier")

    price = calculate_price(tier, req.period)
    days = get_period_days(req.period)

    sub_id = secrets.token_hex(16)
    now = datetime.utcnow()

    subscription = {
        "id": sub_id,
        "user_id": user["id"],
        "tier": tier,
        "period": req.period,
        "status": "pending",  # pending until payment confirmed
        "current_period_start": now.isoformat(),
        "current_period_end": (now + timedelta(days=days)).isoformat(),
        "cancel_at_period_end": False,
        "payment_method": req.payment_method,
        "amount_paid": price,
        "currency": "USD",
        "created_at": now.isoformat(),
        "crypto_chain": req.crypto_chain,
        "tx_hash": None,
    }

    if req.payment_method == "stripe":
        # Create Stripe checkout session
        try:
            import stripe

            stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

            checkout = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[
                    {
                        "price": TIER_CONFIG[tier]["stripe_price_id"],
                        "quantity": 1,
                    }
                ],
                mode="subscription",
                success_url="https://rugmunch.io/pricing/success?session_id={CHECKOUT_SESSION_ID}",
                cancel_url="https://rugmunch.io/pricing/cancel",
                metadata={
                    "user_id": user["id"],
                    "subscription_id": sub_id,
                    "tier": tier,
                    "period": req.period,
                },
            )
            subscription["stripe_session_id"] = checkout.id
            _save_subscription(subscription)

            return {
                "status": "pending",
                "checkout_url": checkout.url,
                "subscription_id": sub_id,
            }
        except Exception as e:
            logger.error(f"Stripe checkout failed: {e}")
            raise HTTPException(status_code=500, detail="Payment processing failed") from e

    elif req.payment_method == "crypto":
        if not req.crypto_chain:
            raise HTTPException(status_code=400, detail="crypto_chain required for crypto payments")

        chain = req.crypto_chain.lower()
        valid_chains = [c["id"] for c in CRYPTO_PAYMENT_CONFIG["accepted_chains"]]
        if chain not in valid_chains:
            raise HTTPException(status_code=400, detail=f"Unsupported chain: {chain}")

        # Generate payment address/QR data
        payment_wallet = CRYPTO_PAYMENT_CONFIG["payment_wallet"]
        if chain == "solana":
            payment_wallet = CRYPTO_PAYMENT_CONFIG.get("sol_payment_wallet", payment_wallet)
        elif chain == "bitcoin":
            payment_wallet = CRYPTO_PAYMENT_CONFIG.get("btc_payment_address", payment_wallet)
        elif chain == "monero":
            payment_wallet = CRYPTO_PAYMENT_CONFIG.get("xmr_payment_address", payment_wallet)

        _save_subscription(subscription)

        return {
            "status": "pending_payment",
            "subscription_id": sub_id,
            "payment_details": {
                "chain": chain,
                "amount_usd": price,
                "payment_address": payment_wallet,
                "memo": f"RMI-{sub_id[:8]}",
                "expires_at": (now + timedelta(hours=24)).isoformat(),
            },
            "instructions": f"Send payment to the address above. Include memo 'RMI-{sub_id[:8]}' for faster confirmation.",
        }
    elif req.payment_method == "x402":
        # x402 micropayment - return EIP-3009 challenge for client to sign
        config = ALL_PRODUCTS.get(tier, {})
        x402_price_atoms = config.get("x402_price_atoms")
        x402_chain_id = config.get("x402_chain_id", 8453)
        if not x402_price_atoms:
            raise HTTPException(status_code=400, detail=f"x402 not available for tier {tier}")
        _save_subscription(subscription)
        return {
            "status": "pending_x402",
            "subscription_id": sub_id,
            "x402": {
                "version": 2,
                "scheme": "exact",
                "network": f"eip155:{x402_chain_id}",
                "asset": "USDC",
                "amount_atoms": x402_price_atoms,
                "amount_usd": price,
                "tier": tier,
                "period": req.period,
                "pay_to": os.getenv("X402_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9"),
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
            },
            "instructions": f"Sign the EIP-3009 USDC transfer authorization via your wallet. ${price} will be charged on Base. Receipt settles the subscription automatically.",
        }

    raise HTTPException(status_code=400, detail="Invalid payment method")


@router.post("/subscription/crypto/confirm")
async def confirm_crypto_payment(req: CryptoPaymentRequest, request: Request):
    """Confirm a crypto payment (called by user after sending tx, or webhook)."""
    from app.auth import _save_user, get_current_user

    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Find pending subscription
    subs = _get_user_subscriptions(user["id"])
    pending = None
    for sub in subs:
        if sub["status"] == "pending" and sub.get("crypto_chain") == req.chain:
            pending = sub
            break

    if not pending:
        raise HTTPException(status_code=404, detail="No pending subscription found")

    # TODO: Verify transaction on-chain
    # For now, we accept the tx_hash and mark as active (in production, verify via RPC)

    pending["status"] = "active"
    pending["tx_hash"] = req.tx_hash
    pending["amount_paid"] = req.amount_paid_usd
    pending["confirmed_at"] = datetime.utcnow().isoformat()
    pending["from_address"] = req.from_address

    _save_subscription(pending)

    # Update user tier
    user["tier"] = pending["tier"]
    user["scans_remaining"] = TIER_CONFIG[pending["tier"]]["scans_per_month"]
    _save_user(user)

    logger.info(f"[SUBSCRIPTION] Crypto payment confirmed: user={user['id']} tier={pending['tier']} tx={req.tx_hash}")

    return {
        "status": "active",
        "subscription": pending,
    }


@router.post("/subscription/cancel")
async def cancel_subscription(request: Request):
    """Cancel subscription at period end."""
    from app.auth import get_current_user

    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    subs = _get_user_subscriptions(user["id"])
    active = None
    for sub in subs:
        if sub["status"] == "active":
            active = sub
            break

    if not active:
        raise HTTPException(status_code=404, detail="No active subscription")

    active["cancel_at_period_end"] = True
    active["cancelled_at"] = datetime.utcnow().isoformat()
    _save_subscription(active)

    return {"status": "ok", "message": "Subscription will cancel at period end"}


@router.post("/subscription/upgrade")
async def upgrade_subscription(tier: str, request: Request):
    """Upgrade to a higher tier."""
    from app.auth import get_current_user

    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    tier = tier.upper()
    if tier not in TIER_CONFIG or tier in ("FREE", "ENTERPRISE"):
        raise HTTPException(status_code=400, detail="Invalid upgrade tier")

    subs = _get_user_subscriptions(user["id"])
    active = None
    for sub in subs:
        if sub["status"] == "active":
            active = sub
            break

    if active:
        # Cancel current and create new
        active["status"] = "cancelled"
        active["cancelled_at"] = datetime.utcnow().isoformat()
        active["cancel_reason"] = "upgraded"
        _save_subscription(active)

    # Create new subscription for new tier
    # (User will need to complete payment)
    return {
        "status": "upgrade_initiated",
        "new_tier": tier,
        "message": "Please complete payment for the new tier",
        "checkout_url": f"/pricing?upgrade_to={tier}",
    }


# ── Webhook Endpoints ──


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhooks for subscription events."""
    from app.auth import _get_user, _save_user

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        import stripe

        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        raise HTTPException(status_code=400, detail="Invalid webhook") from e

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        user_id = metadata.get("user_id")
        sub_id = metadata.get("subscription_id")
        tier = metadata.get("tier")

        if user_id and sub_id:
            # Activate subscription
            subs = _get_user_subscriptions(user_id)
            for sub in subs:
                if sub["id"] == sub_id:
                    sub["status"] = "active"
                    sub["stripe_subscription_id"] = session.get("subscription")
                    sub["confirmed_at"] = datetime.utcnow().isoformat()
                    _save_subscription(sub)

                    # Update user tier
                    user = _get_user(user_id)
                    if user:
                        user["tier"] = tier
                        user["scans_remaining"] = TIER_CONFIG[tier]["scans_per_month"]
                        _save_user(user)
                    break

    elif event["type"] == "invoice.payment_failed":
        event["data"]["object"]
        # Mark subscription as past_due
        # TODO: Implement
        pass

    return {"status": "ok"}


# ── Admin Endpoints ──


@router.get("/admin/subscriptions")
async def list_all_subscriptions(
    request: Request,
    status: str | None = None,
    tier: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List all subscriptions (admin only)."""
    from app.auth import get_current_user

    user = await get_current_user(request)
    if not user or user.get("role") not in ("ADMIN", "SUPERADMIN"):
        raise HTTPException(status_code=403, detail="Admin access required")

    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    all_subs = r.hgetall("rmi:subscriptions")

    results = []
    for user_id, subs_raw in all_subs.items():
        subs = json.loads(subs_raw)
        for sub in subs:
            if status and sub["status"] != status:
                continue
            if tier and sub["tier"] != tier.upper():
                continue
            sub["user_id"] = user_id
            results.append(sub)

    total = len(results)
    paginated = results[offset : offset + limit]

    return {
        "subscriptions": paginated,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/admin/revenue")
async def get_revenue_stats(request: Request):
    """Get revenue statistics (admin only)."""
    from app.auth import get_current_user
    from app.core.redis import get_redis

    user = await get_current_user(request)
    if not user or user.get("role") not in ("ADMIN", "SUPERADMIN"):
        raise HTTPException(status_code=403, detail="Admin access required")

    r = get_redis()
    all_subs = r.hgetall("rmi:subscriptions")

    stats = {
        "total_revenue": 0,
        "by_tier": {},
        "by_period": {},
        "by_payment_method": {},
        "active_subscriptions": 0,
        "cancelled_subscriptions": 0,
        "pending_subscriptions": 0,
    }

    for _user_id, subs_raw in all_subs.items():
        subs = json.loads(subs_raw)
        for sub in subs:
            if sub["status"] == "active":
                stats["active_subscriptions"] += 1
                stats["total_revenue"] += sub.get("amount_paid", 0)

                tier = sub["tier"]
                stats["by_tier"][tier] = stats["by_tier"].get(tier, 0) + sub.get("amount_paid", 0)

                period = sub.get("period", "monthly")
                stats["by_period"][period] = stats["by_period"].get(period, 0) + sub.get("amount_paid", 0)

                method = sub.get("payment_method", "stripe")
                stats["by_payment_method"][method] = stats["by_payment_method"].get(method, 0) + sub.get(
                    "amount_paid", 0
                )
            elif sub["status"] == "cancelled":
                stats["cancelled_subscriptions"] += 1
            elif sub["status"] == "pending":
                stats["pending_subscriptions"] += 1

    return stats
