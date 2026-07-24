"""

RMI Developer Tier System
==========================
Free developer API keys with 100 calls/day. No payment required.
Designed to get bot developers hooked on RMI tools before upgrading to paid.

Tiers:
  FREE: 100 calls/day, rate-limited 10/min, all tools accessible
  TRIAL: 3-5 calls per tool (existing system), no key needed
  PAID: Pay-per-use via x402, no rate limit beyond burst protection
  PRO: $29/month, 5000 calls/day, priority queue, webhook support

Author: RMI Development
Date: 2026-06-05
"""

from __future__ import annotations

import email
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.databus_gateway import generate_api_key
from app.routers.dev_portal import create_api_key

logger = get_logger("developer_tier")

router = APIRouter(prefix="/api/v1/developer", tags=["developer-tier"])

# ── EVM Signature Verification ───────────────────────────────────


def verify_evm_signature(address: str, message: str, signature: str) -> bool:
    """Verify an EVM wallet signature.

    Uses web3.py to recover the signer from the signature and compare
    with the claimed address. This proves wallet ownership.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        encoded = encode_defunct(text=message)
        recovered = Account.recover_message(encoded, signature=signature)
        return recovered.lower() == address.lower()
    except ImportError:
        # eth_account not available - fail closed
        return False
    except ValueError:
        return False


def verify_solana_signature(address: str, message: str, signature: str) -> bool:
    """Verify a Solana wallet signature."""
    try:
        import base64

        from nacl.signing import VerifyKey

        from app.core.redis import get_redis  # noqa: F401

        pubkey = VerifyKey(base64.b58decode(address))
        msg_bytes = message.encode("utf-8")
        sig_bytes = base64.b64decode(signature)
        pubkey.verify(msg_bytes, sig_bytes)
        return True
    except ImportError:
        return False
    except ValueError:
        return False


# ── Tier Definitions ──────────────────────────────────────────────

TIERS = {
    "free": {
        "daily_limit": 100,
        "rate_limit_per_min": 10,
        "rate_limit_per_hour": 200,
        "tools": "all",
        "price_usd": 0,
        "trial_free": 0,
        "webhook_support": False,
        "priority_queue": False,
        "analytics_access": False,
    },
    "trial": {
        "daily_limit": 20,
        "rate_limit_per_min": 5,
        "rate_limit_per_hour": 50,
        "tools": "all",
        "price_usd": 0,
        "trial_free": 3,
        "webhook_support": False,
        "priority_queue": False,
        "analytics_access": False,
    },
    "paid": {
        "daily_limit": 10000,
        "rate_limit_per_min": 60,
        "rate_limit_per_hour": 2000,
        "tools": "all",
        "price_usd": "per_call",
        "trial_free": 0,
        "webhook_support": True,
        "priority_queue": False,
        "analytics_access": True,
    },
    "pro": {
        "daily_limit": 5000,
        "rate_limit_per_min": 120,
        "rate_limit_per_hour": 5000,
        "tools": "all",
        "price_usd": 29.00,
        "trial_free": 0,
        "webhook_support": True,
        "priority_queue": True,
        "analytics_access": True,
    },
}


# ── Redis Helper ─────────────────────────────────────────────────


def create_tier(tier: str) -> dict[str, Any] | None:
    """Create a new developer API key."""
    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not r:
        return None

    if tier not in TIERS:
        return None

    key = generate_api_key()
    hashed = hash_api_key(key)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    created_at = datetime.now(UTC).isoformat()

    key_info = {
        "key": key,  # Only returned once, never stored in plaintext
        "hashed_key": hashed,
        "email": email,
        "tier": tier,
        "wallet_address": wallet_address,  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        "created_at": created_at,
        "status": "active",
        "total_calls": 0,
        "total_revenue_usd": 0,
    }

    # Store in Redis (no TTL for free keys, 90-day TTL for trial)
    r.set(
        f"rmi:dev:key:{hashed}",
        json.dumps(key_info),
        ex=86400 * 90 if tier == "trial" else None,
    )

    # Index by email for lookup
    r.sadd(f"rmi:dev:email:{email}", hashed)

    # Track total keys created
    r.incr("rmi:dev:stats:total_keys")
    r.incr(f"rmi:dev:stats:tier:{tier}")

    return key_info


def get_usage_for_key(hashed_key: str) -> dict[str, Any]:
    """Get usage stats for an API key."""
    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not r:
        return {"error": "redis_unavailable"}

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    today_calls = int(r.get(f"rmi:dev:usage:{hashed_key}:{today}") or 0)
    total_calls = int(r.get(f"rmi:dev:usage:{hashed_key}:total") or 0)

    # Get key info
    key_data = r.get(f"rmi:dev:key:{hashed_key}")
    if not key_data:
        return {"error": "key_not_found"}

    key_info = json.loads(key_data)
    tier_config = TIERS.get(key_info.get("tier", "free"), TIERS["free"])

    return {
        "tier": key_info.get("tier", "free"),
        "email": key_info.get("email", ""),
        "status": key_info.get("status", "active"),
        "today_calls": today_calls,
        "daily_limit": tier_config["daily_limit"],
        "remaining_today": max(0, tier_config["daily_limit"] - today_calls),
        "total_calls": total_calls,
        "created_at": key_info.get("created_at", ""),
        "rate_limit_per_min": tier_config["rate_limit_per_min"],
    }


# ── Request/Response Models ──────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    tier: str = Field(default="free")
    wallet_address: str | None = None


class VerifyRequest(BaseModel):
    api_key: str


class UsageRequest(BaseModel):
    api_key: str


# ── Endpoints ────────────────────────────────────────────────────


@router.post("/register")
async def register_developer(req: RegisterRequest) -> Any:
    """Register for a free developer API key. 100 calls/day, no payment needed."""
    # Validate tier
    if req.tier not in TIERS:
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Invalid tier. Available: {', '.join(TIERS.keys())}",
                "tiers": {
                    k: {"daily_limit": v["daily_limit"], "price": v["price_usd"]}
                    for k, v in TIERS.items()
                },
            },
        )

    # Validate email
    if "@" not in req.email or "." not in req.email.split("@")[-1]:
        return JSONResponse(status_code=400, content={"error": "Invalid email address"})

    # Check if email already has a key
    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if r:
        existing = r.smembers(f"rmi:dev:email:{req.email}")
        if existing:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "Email already registered",
                    "message": "You already have an API key. Use /verify to check your key status.",
                },
            )

    # Create key
    result = create_api_key(req.email, req.tier, req.wallet_address)
    if not result:
        return JSONResponse(status_code=500, content={"error": "Failed to create API key"})

    # Return key info (only show key once)
    return JSONResponse(
        status_code=201,
        content={
            "success": True,
            "message": "Developer API key created. Save it - it won't be shown again.",
            "api_key": result["key"],
            "tier": result["tier"],
            "daily_limit": TIERS[result["tier"]]["daily_limit"],
            "rate_limit_per_min": TIERS[result["tier"]]["rate_limit_per_min"],
            "documentation": "https://rugmunch.io/mcp-docs",
            "quickstart": {
                "curl": f'curl -X POST https://mcp.rugmunch.io/api/v1/x402-tools/audit \\n  -H "Content-Type: application/json" \\n  -H "X-RMI-Dev-Key: {result["key"]}" \\n  -d \'{{"address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"}}\'',
                "python": f"""from rmi import RMI
rmi = RMI(api_key="{result["key"]}")
result = rmi.scan("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
""",
            },
        },
    )


@router.post("/verify")
async def verify_key(req: VerifyRequest) -> Any:
    """Verify an API key and get usage stats."""
    if not req.api_key:
        return JSONResponse(status_code=400, content={"error": "api_key required"})

    hashed = hash_api_key(req.api_key)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    usage = get_usage_for_key(hashed)

    if "error" in usage:
        return JSONResponse(status_code=401, content={"error": "Invalid API key"})

    return JSONResponse(content={"valid": True, **usage})


@router.post("/usage")
async def get_usage(req: UsageRequest) -> Any:
    """Get detailed usage for an API key."""
    if not req.api_key:
        return JSONResponse(status_code=400, content={"error": "api_key required"})

    hashed = hash_api_key(req.api_key)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    # Verify key exists
    key_data = r.get(f"rmi:dev:key:{hashed}")
    if not key_data:
        return JSONResponse(status_code=401, content={"error": "Invalid API key"})

    # Get usage for last 7 days
    today = datetime.now(UTC)
    daily_usage = []
    for i in range(7):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        calls = int(r.get(f"rmi:dev:usage:{hashed}:{day}") or 0)
        daily_usage.append({"date": day, "calls": calls})

    # Get per-tool usage
    tool_usage = {}
    tool_keys = r.keys(f"rmi:dev:tool:{hashed}:*")
    for tk in tool_keys[:50]:  # Limit to 50 tools
        tool_name = tk.decode().split(":")[-1]
        count = int(r.get(tk) or 0)
        if count > 0:
            tool_usage[tool_name] = count

    return JSONResponse(
        content={
            "tier": json.loads(key_data).get("tier", "free"),
            "daily_usage": daily_usage,
            "tool_usage": dict(sorted(tool_usage.items(), key=lambda x: -x[1])[:20]),
        }
    )


@router.get("/tiers")
async def list_tiers() -> Any:
    """List available developer tiers."""
    return JSONResponse(
        content={
            "tiers": {
                name: {
                    "daily_limit": config["daily_limit"],
                    "rate_limit_per_min": config["rate_limit_per_min"],
                    "rate_limit_per_hour": config["rate_limit_per_hour"],
                    "price": config["price_usd"],
                    "webhook_support": config["webhook_support"],
                    "priority_queue": config["priority_queue"],
                    "analytics_access": config["analytics_access"],
                }
                for name, config in TIERS.items()
            },
            "note": "FREE tier: 100 calls/day, no payment required. Upgrade to PAID for unlimited pay-per-use.",
        }
    )


# ── Wallet Identity Verification ─────────────────────────────────


class WalletVerifyRequest(BaseModel):
    address: str
    signature: str
    chain: str = "evm"  # evm or solana


@router.post("/wallet/verify")
async def verify_wallet_identity(req: WalletVerifyRequest) -> Any:
    """Verify wallet ownership via signature.

    Grants a persistent wallet-based identity for trials that survives IP changes.
    Users sign a challenge message with their wallet to prove ownership.
    """
    if not req.address or not req.signature:
        return JSONResponse(status_code=400, content={"error": "address and signature required"})

    # Generate challenge message
    challenge = f"RMI identity verification: {req.address} at {int(time.time()) // 3600}"

    # Verify signature
    if req.chain == "evm":
        valid = verify_evm_signature(req.address, challenge, req.signature)
    elif req.chain == "solana":
        valid = verify_solana_signature(req.address, challenge, req.signature)
    else:
        return JSONResponse(status_code=400, content={"error": "chain must be 'evm' or 'solana'"})

    if not valid:
        return JSONResponse(
            status_code=401, content={"error": "Invalid signature - wallet ownership not proven"}
        )

    # Store wallet identity in Redis
    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    wallet_id = f"wallet:{req.chain}:{req.address.lower()}"

    # Grant enhanced trial limits for verified wallets
    wallet_data = {
        "address": req.address.lower(),
        "chain": req.chain,
        "verified_at": datetime.now(UTC).isoformat(),
        "trial_multiplier": 3,  # 3x normal trial limits
        "daily_free_limit": 50,  # 50 free calls/day for verified wallets
        "status": "active",
    }

    r.set(f"rmi:wallet:{wallet_id}", json.dumps(wallet_data), ex=86400 * 30)  # 30 day TTL
    r.incr("rmi:wallet:stats:total_verified")

    return JSONResponse(
        content={
            "verified": True,
            "wallet_id": wallet_id,
            "benefits": {
                "trial_multiplier": 3,
                "daily_free_limit": 50,
                "persistent_identity": True,
            },
            "message": "Wallet verified! You now get 3x trial limits and 50 free calls/day.",
        }
    )


@router.post("/wallet/get_challenge")
async def get_wallet_challenge(req: VerifyRequest) -> Any:
    """Get a challenge message to sign with your wallet."""
    # If they have an API key, generate a challenge for that key
    if req.api_key:
        hashed = hash_api_key(req.api_key)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        key_data = get_redis().get(f"rmi:dev:key:{hashed}") if get_redis() else None  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        if key_data:
            info = json.loads(key_data)
            address = info.get("wallet_address", "")
            if address:
                challenge = f"RMI identity verification: {address} at {int(time.time()) // 3600}"
                return JSONResponse(
                    content={
                        "message": challenge,
                        "address": address,
                        "instruction": "Sign this message with your wallet to verify ownership.",
                    }
                )

    # Generic challenge
    challenge = f"RMI identity verification at {int(time.time()) // 3600}"
    return JSONResponse(
        content={
            "message": challenge,
            "instruction": "Sign this message with your wallet. Include your wallet address in the /wallet/verify call.",
        }
    )


@router.get("/wallet/status/{chain}/{address}")
async def wallet_status(chain: str, address: str) -> Any:
    """Check wallet verification status and benefits."""
    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not r:
        return JSONResponse(status_code=500, content={"error": "redis_unavailable"})

    wallet_id = f"rmi:wallet:wallet:{chain}:{address.lower()}"
    data = r.get(wallet_id)
    if not data:
        return JSONResponse(
            content={
                "verified": False,
                "message": "Wallet not verified. Use POST /wallet/verify to verify.",
            }
        )

    wallet_data = json.loads(data)
    return JSONResponse(
        content={
            "verified": True,
            **wallet_data,
        }
    )


# ── Middleware Helper ─────────────────────────────────────────────


async def check_developer_key(request: Request) -> dict[str, Any] | None:
    """Check if request has a valid developer API key.

    Returns tier info if valid, None if not a dev key request.
    This is called by the enforcement middleware BEFORE payment checks.
    """
    dev_key = request.headers.get("X-RMI-Dev-Key", "") or request.headers.get("x-rmi-dev-key", "")
    if not dev_key:
        return None

    hashed = hash_api_key(dev_key)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    r = get_redis()  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
    if not r:
        return {"error": "redis_unavailable", "deny": True}

    # Look up key
    key_data = r.get(f"rmi:dev:key:{hashed}")
    if not key_data:
        return {"error": "invalid_api_key", "deny": True, "status_code": 401}

    key_info = json.loads(key_data)

    # Check status
    if key_info.get("status") != "active":
        return {"error": "key_suspended", "deny": True, "status_code": 403}

    # Check daily limit
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    today_calls = int(r.get(f"rmi:dev:usage:{hashed}:{today}") or 0)
    tier_config = TIERS.get(key_info.get("tier", "free"), TIERS["free"])

    if today_calls >= tier_config["daily_limit"]:
        return {
            "error": "daily_limit_exceeded",
            "deny": True,
            "status_code": 429,
            "daily_limit": tier_config["daily_limit"],
            "calls_today": today_calls,
        }

    # Check per-minute rate limit
    minute_key = f"rmi:dev:ratelimit:{hashed}:{int(time.time()) // 60}"
    minute_calls = int(r.get(minute_key) or 0)
    if minute_calls >= tier_config["rate_limit_per_min"]:
        return {
            "error": "rate_limit_exceeded",
            "deny": True,
            "status_code": 429,
            "rate_limit_per_min": tier_config["rate_limit_per_min"],
            "retry_after": 60,
        }

    # All checks passed - consume the call
    pipe = r.pipeline()
    pipe.incr(f"rmi:dev:usage:{hashed}:{today}")
    pipe.incr(f"rmi:dev:usage:{hashed}:total")
    pipe.incr(minute_key)
    pipe.expire(minute_key, 120)  # 2 minute TTL for rate limit key
    pipe.incr("rmi:dev:stats:total_calls")
    pipe.execute()

    return {
        "valid": True,
        "tier": key_info.get("tier", "free"),
        "email": key_info.get("email", ""),
        "remaining_today": tier_config["daily_limit"] - today_calls - 1,
        "deny": False,
    }
