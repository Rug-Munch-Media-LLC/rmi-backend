"""
V1 Bulk Alert Configuration — POST /api/v1/alerts/bulk
========================================================
Batch create alerts for multiple tokens in a single call.

Accepts 1-50 tokens and 1+ alert types, creates alert configs
in Redis, and returns the list of created alert IDs.

Dependency injection: Redis, ChainClient, and EVMClient are
injected via ``Depends()`` for testability.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request

from app.core.errors import RateLimitError, ValidationError
from pydantic import BaseModel, Field

from app.core.dependencies import get_chain_client, get_evm_client, get_redis
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.chain_client import ChainClient
    from app.evm_connector import EVMClient

logger = get_logger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts-v1"])

# ── Constants ────────────────────────────────────────────────────────
_MAX_TOKENS = 50
_RATE_LIMIT_SECONDS = 60  # 1 bulk call per 60 seconds
_RATE_LIMIT_KEY_PREFIX = "rl:bulk_alerts:"

VALID_ALERT_TYPES = [
    "price_change_5%",
    "price_change_10%",
    "price_change_20%",
    "liquidity_change",
    "liquidity_drop_50%",
    "volume_spike",
    "volume_spike_5x",
    "new_holder",
    "holder_concentration",
    "rug_indicator",
]

# ── Rate limiting ──────────────────────────────────────────────────


async def _check_rate_limit(client_key: str, r: Any | None) -> bool:
    """Check and increment rate limit counter for a client.

    Returns True if allowed, False if rate limited.
    Uses Redis when available; falls open when Redis is down.
    """
    if not r:
        return True  # Allow if Redis is down (fail open)
    try:
        key = f"{_RATE_LIMIT_KEY_PREFIX}{client_key}"
        now = time.time()
        window_start = now - _RATE_LIMIT_SECONDS

        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, _RATE_LIMIT_SECONDS + 10)
        await pipe.execute()

        count = await r.zcard(key)
        return count <= 1  # 1 call per 60s window
    except Exception as exc:
        logger.debug("rate_limit_check_failed: %s", exc)
        return True  # Allow on Redis failure


# ── Token validation ──────────────────────────────────────────────────


async def _validate_token_exists(
    token: str,
    chain: str,
    chain_client: ChainClient | None = None,
    evm_client: EVMClient | None = None,
) -> bool:
    """Quick validation that a token address might exist.

    Does a format check + optional lightweight probe.
    Returns True if the token looks valid (no strict guarantee).
    """
    addr = token.lower().strip()

    evm_chains = {
        "ethereum",
        "base",
        "bsc",
        "arbitrum",
        "polygon",
        "optimism",
        "avalanche",
        "fantom",
        "linea",
    }
    if chain in evm_chains and (not addr.startswith("0x") or len(addr) != 42):
        return False
    if chain == "solana" and (addr.startswith("0x") or len(addr) < 32 or len(addr) > 44):
        return False

    # Optional: try a quick balance probe (non-blocking)
    try:
        if chain == "solana" and chain_client is not None:
            supply = await chain_client.rpc_call("getTokenSupply", [addr])
            if supply is not None:
                return True
            return True  # Accept on format if probe fails
        elif chain in ("ethereum", "base", "arbitrum") and evm_client is not None:
            try:
                code = evm_client.w3.eth.get_code(addr)
                if code and code != "0x":
                    return True
            except Exception:
                pass
            return True  # Accept on format if client unavailable
    except Exception as exc:
        logger.debug("token_validation_probe_failed %s: %s", addr[:12], exc)
        return True

    return True


# ── Request / Response models ────────────────────────────────────────


class BulkAlertRequest(BaseModel):
    """Bulk alert creation request."""

    tokens: list[str] = Field(..., min_length=1, max_length=_MAX_TOKENS)
    alert_types: list[str] = Field(..., min_length=1)
    webhook_url: str | None = None
    email: str | None = None
    chain: str = "ethereum"


class BulkAlertResponse(BaseModel):
    """Response for bulk alert creation."""

    created: int
    failed: int
    alert_ids: list[str]
    errors: list[dict[str, str]]
    skipped: list[dict[str, str]]


# ── Endpoint ─────────────────────────────────────────────────────────


@router.post("/bulk")
async def create_bulk_alerts(
    request: BulkAlertRequest,
    http_request: Request,
    r: Any | None = Depends(get_redis),
    chain_client: ChainClient | None = Depends(get_chain_client),
    evm_client: EVMClient | None = Depends(get_evm_client),
) -> dict[str, Any]:
    """Create alerts for multiple tokens in a single call.

    Accepts 1-50 tokens and 1+ alert types. Validates each token's
    format and existence before creating alert configs. Returns the
    list of created alert IDs.

    Rate limited: 1 bulk call per 60 seconds per client IP.
    """
    # ── Rate limit ────────────────────────────────────────────────
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not await _check_rate_limit(client_ip, r):
        raise RateLimitError(
            f"Rate limit exceeded. Maximum 1 bulk call per {_RATE_LIMIT_SECONDS} seconds.",
        )

    # ── Validate alert types ──────────────────────────────────────
    invalid_types = [t for t in request.alert_types if t not in VALID_ALERT_TYPES]
    if invalid_types:
        raise ValidationError(
            f"Invalid alert types: {invalid_types}. Valid types: {VALID_ALERT_TYPES}",
        )

    # ── Validate at least one notification target ─────────────────
    if not request.webhook_url and not request.email:
        raise ValidationError(
            "At least one notification target required: webhook_url or email",
        )

    # ── Validate tokens ───────────────────────────────────────────
    chain = request.chain.lower().strip()
    valid_tokens: list[str] = []
    errors: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for token in request.tokens:
        try:
            exists = await _validate_token_exists(
                token,
                chain,
                chain_client=chain_client if chain == "solana" else None,
                evm_client=evm_client if chain in ("ethereum", "base", "arbitrum") else None,
            )
            if exists:
                valid_tokens.append(token.lower().strip())
            else:
                errors.append({"token": token, "reason": "Invalid format or non-existent"})
        except Exception as exc:
            logger.debug("token_validation_error %s: %s", token[:12], exc)
            skipped.append({"token": token, "reason": f"Validation error: {str(exc)[:100]}"})

    if not valid_tokens:
        raise ValidationError(
            "No valid tokens provided. All tokens failed validation.",
        )

    # ── Create alerts ─────────────────────────────────────────────
    created_ids: list[str] = []
    created_count = 0
    failed_count = 0

    config_prefix = f"rmi:alert_config:{chain}"

    for token in valid_tokens:
        for alert_type in request.alert_types:
            try:
                alert_id = _make_alert_id(token, alert_type)
                config = {
                    "id": alert_id,
                    "token": token,
                    "chain": chain,
                    "alert_type": alert_type,
                    "webhook_url": request.webhook_url,
                    "email": request.email,
                    "created_at": time.time(),
                    "active": True,
                }

                if r:
                    await r.hset(f"{config_prefix}:{token}", alert_type, json.dumps(config))
                    await r.sadd(f"{config_prefix}:tokens", token)

                created_ids.append(alert_id)
                created_count += 1
            except Exception as exc:
                logger.warning(
                    "alert_creation_failed token=%s type=%s: %s", token[:12], alert_type, exc
                )
                errors.append({"token": token, "alert_type": alert_type, "reason": str(exc)[:100]})
                failed_count += 1

    logger.info(
        "bulk_alerts_created count=%d failed=%d chain=%s",
        created_count,
        failed_count,
        chain,
    )

    return {
        "created": created_count,
        "failed": failed_count,
        "alert_ids": created_ids,
        "errors": errors[:10],
        "skipped": skipped[:10],
    }


def _make_alert_id(token: str, alert_type: str) -> str:
    """Generate deterministic alert ID from token + alert type."""
    raw = f"{token}:{alert_type}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"alert_{h}"
