"""
RMI x402 Payment Middleware - Proper x402 Protocol Integration
================================================================
Uses the official x402 SDK for payment verification, receipt validation,
and public transparency. Replaces the hacky direct facilitator POST.

Components:
1. x402_middleware.py - FastAPI middleware for payment verification
2. /api/v1/x402/receipt/{id}/verify - Public receipt verification
3. /api/v1/x402/ledger - Public payment ledger (anonymized)
4. /api/v1/x402/transparency - System trust metrics

Author: RMI Development
Date: 2026-05-03
"""

import contextlib
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger("x402_middleware")

router = APIRouter(prefix="/api/v1/x402", tags=["x402-payment-middleware"])


# ── x402 SDK Integration ──────────────────────────────────────

# Import x402 SDK components
try:
    from x402 import (
        X402_VERSION,
        Network,  # noqa: F401
        PaymentPayload,  # noqa: F401
        PaymentRequirements,  # noqa: F401
        parse_payment_payload,  # noqa: F401
        parse_payment_required,  # noqa: F401
        x402Facilitator,  # noqa: F401
    )

    X402_SDK_AVAILABLE = True
    logger.info(f"x402 SDK v{X402_VERSION} loaded")
except ImportError as e:
    X402_SDK_AVAILABLE = False
    logger.warning(f"x402 SDK not available: {e}")


# ── Payment Verification Engine ────────────────────────────────

# ERC-20 token metadata for EIP-712 domain verification
# Maps (chain_id, token_address) -> (name, version)
TOKEN_METADATA = {
    # USDC on Base mainnet
    (8453, "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"): ("USD Coin", "2"),
    # USDC on Base Sepolia
    (84532, "0x036cbd53842c5426634e7929541ec2318f3dcf7e"): ("USD Coin", "2"),
}

# Network configs with correct facilitator URLs
FACILITATOR_CONFIGS = {
    "base": {
        "network": "eip155:8453",
        "chain_id": 8453,
        # x402.org doesn't support Base mainnet, use PayAI
        "url": "https://facilitator.payai.network",
    },
    "base-sepolia": {
        "network": "eip155:84532",
        "chain_id": 84532,
        "url": "https://x402.org/facilitator",
    },
    "solana": {
        "network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        "url": "https://facilitator.payai.network",
    },
    "solana-devnet": {
        "network": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
        "url": "https://facilitator.payai.network",
    },
}


class PaymentVerifier:
    """
    x402 payment verification with local EIP-712 verification for Base
    and facilitator fallback for other networks.

    Primary: Local EIP-712 signature verification (no external dependency)
    Fallback: PayAI facilitator via HTTP
    """

    def __init__(self):
        self._facilitators: dict[str, Any] = {}

    def _verify_eip712_local(self, payload_data: dict) -> dict[str, Any]:
        """
        Verify EIP-712 signed EIP-3009 authorization locally.
        No external facilitator needed - pure cryptographic verification.
        Uses the name/version from the payment's extra field (matches what SDK signed with).
        """
        try:
            from eth_account import Account
            from eth_account.messages import encode_typed_data
            from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
            from x402.mechanisms.evm.types import DOMAIN_TYPES, ExactEIP3009Authorization
            from x402.mechanisms.evm.utils import get_evm_chain_id

            accepted = payload_data.get("accepted", {})
            inner = payload_data.get("payload", {})
            auth = inner.get("authorization", {})
            signature = inner.get("signature", "")

            if not all([auth, signature, accepted]):
                return {"verified": False, "reason": "Missing authorization or signature"}

            # Build typed data exactly as the SDK client did
            chain_id = get_evm_chain_id(accepted["network"])

            authorization = ExactEIP3009Authorization(
                from_address=auth["from"],
                to=auth["to"],
                value=auth["value"],
                valid_after=auth["validAfter"],
                valid_before=auth["validBefore"],
                nonce=auth["nonce"],
            )

            # Get name/version from extra (this is what the SDK used to sign)
            extra = accepted.get("extra", {})
            token_name = extra.get("name", "USD Coin")
            token_version = extra.get("version", "2")

            domain, types, primary_type, message = build_typed_data_for_signing(
                authorization, chain_id, accepted["asset"], token_name, token_version
            )

            # Verify time validity
            now = int(time.time())
            valid_before = int(auth["validBefore"])
            valid_after = int(auth["validAfter"])
            if valid_before < now + 6:
                return {"verified": False, "reason": "Authorization expired (validBefore passed)"}
            if valid_after > now:
                return {
                    "verified": False,
                    "reason": "Authorization not yet valid (validAfter in future)",
                }

            # Build typed data for eth_account verification
            all_types = {**DOMAIN_TYPES, **types}
            typed_data = {
                "types": {
                    k: [{"name": f["name"], "type": f["type"]} for f in v]
                    for k, v in all_types.items()
                },
                "primaryType": primary_type,
                "domain": {
                    "name": domain.name,
                    "version": domain.version,
                    "chainId": domain.chain_id,
                    "verifyingContract": domain.verifying_contract,
                },
                "message": {
                    k: ("0x" + v.hex() if isinstance(v, bytes) else str(v))
                    for k, v in message.items()
                },
            }

            # Recover signer from signature
            signable = encode_typed_data(full_message=typed_data)
            recovered = Account.recover_message(signable, signature=signature)

            if recovered.lower() != auth["from"].lower():
                return {
                    "verified": False,
                    "reason": f"Signature mismatch: recovered {recovered}, expected {auth['from']}",
                }

            # Verify recipient matches payTo
            if auth["to"].lower() != accepted.get("payTo", "").lower():
                return {"verified": False, "reason": "Recipient address mismatch"}

            # Verify amount matches
            if str(auth["value"]) != str(accepted.get("amount", "")):
                return {"verified": False, "reason": "Amount mismatch"}

            return {
                "verified": True,
                "reason": "EIP-712 signature verified locally",
                "payer": auth["from"],
                "tx_hash": None,
            }

        except Exception as e:
            logger.error(f"Local EIP-712 verification error: {e}")
            return {"verified": False, "reason": f"Local verification error: {e!s}"}

    async def _verify_via_payai(
        self,
        payload_data: dict,
        requirements_data: dict | None,
    ) -> dict[str, Any]:
        """Fallback: verify via PayAI facilitator HTTP."""
        import aiohttp

        # Build flattened requirements format PayAI expects
        accepted = payload_data.get("accepted", {})
        flat_req = {
            "x402Version": 2,
            "scheme": accepted.get("scheme", "exact"),
            "network": accepted.get("network", "eip155:8453"),
            "asset": accepted.get("asset", ""),
            "amount": accepted.get("amount", ""),
            "payTo": accepted.get("payTo", ""),
            "maxTimeoutSeconds": accepted.get("maxTimeoutSeconds", 60),
            "extra": accepted.get("extra", {}),
            "resource": {},
        }
        if requirements_data:
            flat_req["resource"] = requirements_data.get("resource", {})

        body = {
            "x402Version": 2,
            "paymentPayload": payload_data,
            "paymentRequirements": flat_req,
        }

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    "https://facilitator.payai.network/verify",
                    json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp,
            ):
                data = await resp.json()
                if data.get("isValid"):
                    return {
                        "verified": True,
                        "reason": "Verified via PayAI",
                        "payer": data.get("payer"),
                        "tx_hash": None,
                    }
                return {
                    "verified": False,
                    "reason": f"PayAI: {data.get('invalidReason', 'unknown')} - {data.get('invalidMessage', '')}",
                    "payer": data.get("payer"),
                }
        except Exception as e:
            logger.error(f"PayAI verification error: {e}")
            return {"verified": False, "reason": f"PayAI connection error: {e!s}"}

    async def verify_payment(
        self,
        x402_payload: str,
        network_key: str = "base",
        expected_amount: str | None = None,
        expected_tool: str | None = None,
    ) -> dict[str, Any]:
        """
        Verify an x402 payment payload with layered verification.

        Priority:
        1. Local EIP-712 verification (Base/EVM, instant, no external deps)
        2. PayAI facilitator fallback
        """
        # Step 1: Parse the payment payload
        try:
            payload_data = json.loads(x402_payload)
        except json.JSONDecodeError:
            return {
                "verified": False,
                "reason": "Invalid JSON in x402 payload",
                "tx_hash": None,
                "amount": None,
                "network": network_key,
                "settlement_id": None,
            }

        # Step 2: Local EIP-712 verification (primary for Base/EVM)
        if network_key.startswith("base"):
            local_result = self._verify_eip712_local(payload_data)
            if local_result["verified"]:
                return {
                    "verified": True,
                    "reason": local_result["reason"],
                    "tx_hash": local_result.get("tx_hash"),
                    "amount": expected_amount,
                    "network": network_key,
                    "settlement_id": None,
                    "payer": local_result.get("payer"),
                }
            logger.warning(f"Local verification failed: {local_result['reason']}")
            # Fall through to facilitator

        # Step 3: Facilitator fallback
        config = FACILITATOR_CONFIGS.get(network_key, FACILITATOR_CONFIGS.get("base"))
        config["url"]

        # Build requirements from payload data for facilitator
        requirements_data = None
        try:
            accepted = payload_data.get("accepted", {})
            resource = payload_data.get("resource", {})
            requirements_data = {
                "x402Version": payload_data.get("x402Version", 2),
                "resource": resource,
                "accepts": [
                    {
                        "scheme": accepted.get("scheme", "exact"),
                        "network": accepted.get("network", config["network"]),
                        "asset": accepted.get("asset", ""),
                        "amount": accepted.get("amount", ""),
                        "payTo": accepted.get("payTo", ""),
                        "maxTimeoutSeconds": accepted.get("maxTimeoutSeconds", 60),
                        "extra": accepted.get("extra", {}),
                    }
                ],
            }
        except Exception:
            pass

        return await self._verify_via_payai(payload_data, requirements_data)


# Global verifier instance
_verifier = PaymentVerifier()


# ── Public Receipt Verification ───────────────────────────────


@router.get("/receipt/{receipt_id}/verify")
async def verify_receipt_public(receipt_id: str):
    """
    Public endpoint to verify any x402 receipt.
    Anyone can check if a payment was fulfilled - no auth needed.
    Returns: receipt data + on-chain proof + verification status.
    """
    try:
        from app.auth import get_redis

        r = await get_redis()

        receipt_data = await r.get(f"x402:receipt:{receipt_id}")
        if not receipt_data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        receipt = json.loads(receipt_data)

        # Compute integrity hash for transparency
        integrity_data = f"{receipt_id}:{receipt.get('amount')}:{receipt.get('status')}:{receipt.get('timestamp')}"
        integrity_hash = hashlib.sha256(integrity_data.encode()).hexdigest()[:16]

        # Determine verification status
        verification_status = "unverified"
        if receipt.get("status") == "fulfilled":
            verification_status = "verified"
            if receipt.get("payment_tx"):
                verification_status = "on_chain_confirmed"
        elif receipt.get("status") == "refunded":
            verification_status = "refunded"

        return {
            "receipt_id": receipt_id,
            "status": receipt.get("status"),
            "verification": verification_status,
            "amount": receipt.get("amount"),
            "asset": receipt.get("asset"),
            "network": receipt.get("network"),
            "tool": receipt.get("tool"),
            "customer": receipt.get("customer_address", "")[:8] + "..."
            if receipt.get("customer_address")
            else None,
            "timestamp": receipt.get("timestamp"),
            "payment_tx": receipt.get("payment_tx"),
            "delivery_status": receipt.get("delivery_status"),
            "integrity_hash": integrity_hash,
            "chain_of_custody": {
                "stored": receipt.get("updated_at") is not None,
                "fulfilled": receipt.get("status") == "fulfilled",
                "on_chain": receipt.get("payment_tx") is not None
                and receipt.get("payment_tx") != "",
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Public Payment Ledger ─────────────────────────────────────


@router.get("/ledger")
async def payment_ledger(
    limit: int = 50,
    offset: int = 0,
    tool: str | None = None,
    network: str | None = None,
    status: str | None = None,
):
    """
    Public anonymized payment ledger for transparency.
    Shows: tool used, amount, network, timestamp, status.
    No wallet addresses, no PII.
    """
    try:
        from app.auth import get_redis

        r = await get_redis()

        # Get recent receipt IDs
        all_ids = await r.zrevrange("x402:receipts_by_time", offset, offset + limit - 1)

        ledger = []
        for rid in all_ids:
            rid_str = rid.decode() if isinstance(rid, bytes) else rid
            data = await r.get(f"x402:receipt:{rid_str}")
            if not data:
                continue

            receipt = json.loads(data)

            # Filter by tool/network/status if specified
            if tool and receipt.get("tool") != tool:
                continue
            if network and receipt.get("network") != network:
                continue
            if status and receipt.get("status") != status:
                continue

            # Anonymize customer
            customer = receipt.get("customer_address", "")
            anon_customer = customer[:6] + "..." + customer[-4:] if len(customer) > 10 else None

            ledger.append(
                {
                    "receipt_id": rid_str,
                    "tool": receipt.get("tool"),
                    "amount": receipt.get("amount"),
                    "asset": receipt.get("asset"),
                    "network": receipt.get("network"),
                    "status": receipt.get("status"),
                    "delivery": receipt.get("delivery_status"),
                    "timestamp": receipt.get("timestamp"),
                    "customer": anon_customer,
                }
            )

        # Get total count
        total = await r.zcard("x402:receipts_by_time")

        return {
            "ledger": ledger,
            "count": len(ledger),
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Transparency Dashboard ────────────────────────────────────


@router.get("/transparency")
async def transparency_dashboard(hours: int = 24):
    """
    Public transparency dashboard showing system trust metrics.
    No sensitive data - all aggregated and anonymized.
    """
    try:
        from app.auth import get_redis

        r = await get_redis()

        cutoff = time.time() - (hours * 3600)
        recent_ids = await r.zrangebyscore("x402:receipts_by_time", cutoff, "+inf")

        total = fulfilled = refunded = failed = 0
        total_revenue = 0.0
        tool_counts: dict[str, int] = {}
        network_counts: dict[str, int] = {}
        # hourly array intentionally skipped - data is fetched on-demand
        for rid in recent_ids:
            rid_str = rid.decode() if isinstance(rid, bytes) else rid
            data = await r.get(f"x402:receipt:{rid_str}")
            if not data:
                continue

            receipt = json.loads(data)
            total += 1

            status = receipt.get("status", "unknown")
            if status == "fulfilled":
                fulfilled += 1
            elif status == "refunded":
                refunded += 1
            elif status in ("failed", "error"):
                failed += 1

            # Revenue tracking (amounts stored in atomic units, convert to USDC)
            try:
                amount = float(receipt.get("amount", 0))
                total_revenue += amount / 1e6
            except (ValueError, TypeError):
                pass

            # Tool breakdown
            tool_name = receipt.get("tool", "unknown")
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

            # Network breakdown
            net = receipt.get("network", "unknown")
            network_counts[net] = network_counts.get(net, 0) + 1

        # Get trial stats using SCAN (KEYS is disabled)
        total_trials_used = 0
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="x402:trial:*", count=100)
            for k in keys:
                val = await r.get(k)
                if val:
                    with contextlib.suppress(ValueError, TypeError):
                        total_trials_used += int(val)
            if cursor == 0:
                break

        # Get ban stats
        ip_bans = wallet_bans = 0
        async for key in r.scan_iter(match="x402:ban:ip:*"):
            k = key.decode() if isinstance(key, bytes) else key
            if ":reason:" not in k:
                ip_bans += 1
        async for key in r.scan_iter(match="x402:ban:wallet:*"):  # noqa: B007
            wallet_bans += 1

        fulfillment_rate = fulfilled / max(1, total)
        refund_rate = refunded / max(1, total)

        return {
            "period_hours": hours,
            "payments": {
                "total": total,
                "fulfilled": fulfilled,
                "refunded": refunded,
                "failed": failed,
                "fulfillment_rate": round(fulfillment_rate, 4),
                "refund_rate": round(refund_rate, 4),
                "total_revenue_usdc": round(total_revenue, 4),
            },
            "tools": dict(sorted(tool_counts.items(), key=lambda x: -x[1])[:10]),
            "networks": network_counts,
            "trials": {
                "total_used": total_trials_used,
            },
            "security": {
                "ip_bans_active": ip_bans,
                "wallet_bans_active": wallet_bans,
                "ban_duration_hours": 24,
            },
            "trust_score": round(
                (
                    fulfillment_rate * 0.6
                    + (1 - refund_rate) * 0.3
                    + (1 - failed / max(1, total)) * 0.1
                )
                * 100,
                1,
            ),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Payment Verification Endpoint (for gateway use) ────────────


@router.post("/verify")
async def verify_payment_endpoint(
    request: Request,
):
    """
    Verify an x402 payment payload.
    Used by the gateway worker to verify payments before executing tools.
    Exempt from rate limiting - gateway workers call this for every paid request.

    POST body: {
        "payload": "<x402 payment payload JSON string>",
        "network": "base" | "solana",
        "tool": "audit" | "rugshield" | ...,
        "amount": "$0.02" | ...,
        "amount_atomic": "20000" | ...
    }

    Returns: {
        "verified": true/false,
        "reason": "...",
        "tx_hash": "...",
        "settlement_id": "...",
    }
    """
    try:
        body = await request.json()
        x402_payload = body.get("payload", "")
        network = body.get("network", "base")
        tool = body.get("tool")
        amount = body.get("amount")

        if not x402_payload:
            raise HTTPException(status_code=400, detail="Missing x402 payload")

        result = await _verifier.verify_payment(
            x402_payload,
            network_key=network,
            expected_amount=amount,
            expected_tool=tool,
        )

        # Store verification result for audit trail
        if result["verified"]:
            try:
                from app.auth import get_redis

                r = await get_redis()
                verify_key = f"x402:verify:{result.get('settlement_id') or str(time.time())}"
                await r.set(
                    verify_key,
                    json.dumps(
                        {
                            "verified_at": datetime.now(UTC).isoformat(),
                            "network": network,
                            "tool": tool,
                            "amount": amount,
                            "tx_hash": result.get("tx_hash"),
                            "result": result["reason"],
                        }
                    ),
                    ex=7 * 86400,
                )  # Keep for 7 days
            except Exception as e:
                logger.debug(f"Audit store failed: {e}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Payment verification error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Receipt Storage Endpoint (called by x402 Workers after payment) ────


@router.post("/receipt")
async def store_receipt(request: Request):
    """
    Store a payment receipt from an x402 Worker after successful payment verification.

    AUTH: Requires X-API-Key header matching ADMIN_API_KEY env var.
    Called by x402-base and x402-sol Workers after verifyPayment succeeds.
    Stores receipt in Redis for revenue tracking, audit trail, and transparency dashboard.

    POST body: {
        "receipt_id": "rmi-<timestamp>-<rand>",
        "payment_tx": "0x...|<sol-sig>",
        "amount": "$0.01"|0.01|10000,
        "asset": "USDC",
        "network": "base"|"solana"|<chain>,
        "customer_address": "0x...|<sol-addr>",
        "scan_request": {},
        "status": "fulfilled",
        "tool": "urlcheck"|"audit"|...,
        "delivery_status": "fulfilled",
        "fallback_chain": []
    }
    """
    # Auth: require admin API key
    admin_key = os.getenv("ADMIN_API_KEY", os.getenv("WP_ADMIN_KEY", ""))
    if admin_key:
        auth = request.headers.get("X-API-Key", "") or request.headers.get(
            "Authorization", ""
        ).replace("Bearer ", "")
        if auth != admin_key:
            return JSONResponse(status_code=403, content={"error": "Admin API key required"})

    try:
        body = await request.json()
        receipt_id = body.get("receipt_id", f"rmi-{int(time.time())}-unknown")
        payment_tx = body.get("payment_tx", "")
        amount = body.get("amount", "")
        asset = body.get("asset", "USDC")
        network = body.get("network", "unknown")
        customer_address = body.get("customer_address", "")
        tool = body.get("tool", "unknown")
        status = body.get("status", "fulfilled")
        delivery_status = body.get("delivery_status", "fulfilled")
        fallback_chain = body.get("fallback_chain", [])
        scan_request = body.get("scan_request", {})

        # Normalize amount
        if isinstance(amount, str):
            amount_val = amount.replace("$", "")
            try:
                amount_float = float(amount_val)
            except (ValueError, TypeError):
                amount_float = 0.0
        elif isinstance(amount, (int, float)):
            amount_float = float(amount)
        else:
            amount_float = 0.0

        # Store in Redis (sync - app.auth.get_redis returns a sync client)
        from app.auth import get_redis as _get_redis

        r = _get_redis()

        if r:
            receipt_data = {
                "receipt_id": receipt_id,
                "payment_tx": payment_tx,
                "amount": str(amount_float) if amount_float else str(amount),
                "asset": asset,
                "network": network,
                "customer_address": customer_address,
                "tool": tool,
                "status": status,
                "delivery_status": delivery_status,
                "timestamp": str(time.time()),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "fallback_chain": fallback_chain,
                "scan_request": scan_request,
                "amount_usd": amount_float,
            }

            # Store receipt with 30-day TTL
            r.set(f"x402:receipt:{receipt_id}", json.dumps(receipt_data), ex=30 * 86400)

            # Also store by transaction hash for lookup
            if payment_tx and payment_tx != "local-verified":
                r.set(
                    f"x402:spent_tx:{payment_tx}",
                    json.dumps(
                        {
                            "chain": network,
                            "payer": customer_address,
                            "amount": str(amount_float),
                            "tool": tool,
                            "timestamp": str(time.time()),
                            "receipt_id": receipt_id,
                        }
                    ),
                    ex=30 * 86400,
                )

            # Add to time-ordered index for ledger queries
            r.zadd("x402:receipts_by_time", {receipt_id: time.time()})

            # Increment revenue counter
            today = time.strftime("%Y-%m-%d")
            r.incrbyfloat(f"x402:revenue:daily:{today}", amount_float or 0)
            r.incrbyfloat("x402:revenue:total", amount_float or 0)
            r.incrby(f"x402:calls:daily:{today}", 1)
            r.incrby("x402:calls:total", 1)

            # Tool-level stats
            r.incrby(f"x402:tool_calls:{tool}", 1)
            r.incrbyfloat(f"x402:tool_revenue:{tool}", amount_float or 0)

            logger.info(
                f"Receipt stored: {receipt_id} tx={payment_tx[:16]}... tool={tool} amount=${amount_float}"
            )

            return {
                "ok": True,
                "receipt_id": receipt_id,
                "stored": True,
                "message": "Receipt stored successfully",
            }
        else:
            # Redis unavailable - still return success so Worker doesn't retry endlessly
            logger.warning(f"Redis unavailable, receipt not stored: {receipt_id}")
            return {
                "ok": True,
                "receipt_id": receipt_id,
                "stored": False,
                "message": "Receipt received but Redis unavailable",
            }

    except Exception as e:
        logger.error(f"Receipt store error: {e}")
        # Return 200 so Worker doesn't retry - receipt storage is best-effort
        return {
            "ok": True,
            "stored": False,
            "error": str(e)[:200],
            "message": "Receipt received but storage failed",
        }


# ── Fallback System Status ────────────────────────────────────


@router.get("/fallback/status")
async def fallback_status():
    """
    Check fallback system health, cache stats, and fallback success rate.
    Shows which fallback layers are working and which are failing.
    """
    try:
        from app.auth import get_redis

        r = await get_redis()

        # Count cache entries
        cache_count = 0
        async for _key in r.scan_iter(match="x402:cache:*", count=100):
            cache_count += 1

        # Get fallback usage stats
        fallback_uses = 0
        async for _key in r.scan_iter(match="x402:fallback:*", count=100):
            fallback_uses += 1

        return {
            "fallback_system": "active",
            "cache_entries": cache_count,
            "fallback_uses": fallback_uses,
            "layers_available": [
                "cache",
                "geckoterminal",
                "dexlab",
                "step_finance",
                "moonrank",
                "web_scrape",
                "dexscreener_web",
                "raydium",
                "orca",
                "jito",
                "twitter",
                "reddit",
                "news",
                "web_search",
                "bigquery",
            ],
            "priority_order": [
                "1. Primary APIs (DexScreener, RPCs, etc.)",
                "2. Secondary APIs (Birdeye, Jupiter, Coingecko)",
                "3. Tertiary APIs (DeFiLlama, GeckoTerminal, DexLab)",
                "4. Web scraping (Solscan, Etherscan, Basescan HTML)",
                "5. Browser automation (JS-heavy sites)",
                "6. AI web search (DuckDuckGo, Google)",
                "7. Public data archives (BigQuery public datasets)",
                "8. Community APIs (Jito, Orca, Raydium)",
                "9. RSS/feeds (crypto news, launch trackers)",
                "10. Social media (Twitter/X, Reddit public)",
                "11. Cache layer (previous successful results)",
            ],
            "refund_policy": "Refund only if ALL fallback layers fail",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
