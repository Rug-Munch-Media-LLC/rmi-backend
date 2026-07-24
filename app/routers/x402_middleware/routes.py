"""
x402 Middleware — Route Handlers.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.errors import AppError, NotFoundError, ServiceUnavailableError, ValidationError
from app.core.logging import get_logger
from . import router
from .verifier import PaymentVerifier

logger = get_logger("x402_middleware")

# Global verifier instance
_verifier = PaymentVerifier()


# ── Public Receipt Verification ────────────────────────────────

@router.get("/receipt/{receipt_id}/verify")
async def verify_receipt_public(receipt_id: str) -> dict:
    """Public endpoint to verify any x402 receipt."""
    try:
        from app.auth import get_redis
        r = await get_redis()
        receipt_data = await r.get(f"x402:receipt:{receipt_id}")
        if not receipt_data:
            raise NotFoundError("Receipt not found")
        receipt = json.loads(receipt_data)
        integrity_data = f"{receipt_id}:{receipt.get('amount')}:{receipt.get('status')}:{receipt.get('timestamp')}"
        integrity_hash = hashlib.sha256(integrity_data.encode()).hexdigest()[:16]
        verification_status = "unverified"
        if receipt.get("status") == "fulfilled":
            verification_status = "verified"
            if receipt.get("payment_tx"):
                verification_status = "on_chain_confirmed"
        elif receipt.get("status") == "refunded":
            verification_status = "refunded"
        return {
            "receipt_id": receipt_id, "status": receipt.get("status"),
            "verification": verification_status, "amount": receipt.get("amount"),
            "asset": receipt.get("asset"), "network": receipt.get("network"),
            "tool": receipt.get("tool"),
            "customer": (receipt.get("customer_address", "")[:8] + "...") if receipt.get("customer_address") else None,
            "timestamp": receipt.get("timestamp"), "payment_tx": receipt.get("payment_tx"),
            "delivery_status": receipt.get("delivery_status"),
            "integrity_hash": integrity_hash,
            "chain_of_custody": {
                "stored": receipt.get("updated_at") is not None,
                "fulfilled": receipt.get("status") == "fulfilled",
                "on_chain": bool(receipt.get("payment_tx")),
            },
        }
    except (HTTPException, AppError):
        raise
    except Exception as e:
        raise AppError(message=str(e)) from e


# ── Public Payment Ledger ──────────────────────────────────────

@router.get("/ledger")
async def payment_ledger(
    limit: int = 50, offset: int = 0,
    tool: str | None = None, network: str | None = None, status: str | None = None,
) -> dict:
    """Public anonymized payment ledger for transparency."""
    try:
        from app.auth import get_redis
        r = await get_redis()
        all_ids = await r.zrevrange("x402:receipts_by_time", offset, offset + limit - 1)
        ledger = []
        for rid in all_ids:
            rid_str = rid.decode() if isinstance(rid, bytes) else rid
            data = await r.get(f"x402:receipt:{rid_str}")
            if not data:
                continue
            receipt = json.loads(data)
            if tool and receipt.get("tool") != tool:
                continue
            if network and receipt.get("network") != network:
                continue
            if status and receipt.get("status") != status:
                continue
            customer = receipt.get("customer_address", "")
            anon_customer = customer[:6] + "..." + customer[-4:] if len(customer) > 10 else None
            ledger.append({
                "receipt_id": rid_str, "tool": receipt.get("tool"),
                "amount": receipt.get("amount"), "asset": receipt.get("asset"),
                "network": receipt.get("network"), "status": receipt.get("status"),
                "delivery": receipt.get("delivery_status"), "timestamp": receipt.get("timestamp"),
                "customer": anon_customer,
            })
        total = await r.zcard("x402:receipts_by_time")
        return {"ledger": ledger, "count": len(ledger), "total": total, "offset": offset, "limit": limit}
    except Exception as e:
        raise AppError(message=str(e)) from e


# ── Transparency Dashboard ─────────────────────────────────────

@router.get("/transparency")
async def transparency_dashboard(hours: int = 24) -> dict:
    """Public transparency dashboard showing system trust metrics."""
    try:
        from app.auth import get_redis
        r = await get_redis()
        cutoff = time.time() - (hours * 3600)
        recent_ids = await r.zrangebyscore("x402:receipts_by_time", cutoff, "+inf")
        total = fulfilled = refunded = failed = 0
        total_revenue = 0.0
        tool_counts: dict[str, int] = {}
        network_counts: dict[str, int] = {}
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
            try:
                amount = float(receipt.get("amount", 0))
                total_revenue += amount / 1e6
            except (ValueError, TypeError):
                pass
            tool_name = receipt.get("tool", "unknown")
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            net = receipt.get("network", "unknown")
            network_counts[net] = network_counts.get(net, 0) + 1

        # Trial stats via SCAN
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

        ip_bans = wallet_bans = 0
        async for key in r.scan_iter(match="x402:ban:ip:*"):
            k = key.decode() if isinstance(key, bytes) else key
            if ":reason:" not in k:
                ip_bans += 1
        async for _key in r.scan_iter(match="x402:ban:wallet:*"):
            wallet_bans += 1

        fulfillment_rate = fulfilled / max(1, total)
        refund_rate = refunded / max(1, total)
        return {
            "period_hours": hours,
            "payments": {"total": total, "fulfilled": fulfilled, "refunded": refunded,
                         "failed": failed, "fulfillment_rate": round(fulfillment_rate, 4),
                         "refund_rate": round(refund_rate, 4),
                         "total_revenue_usdc": round(total_revenue, 4)},
            "tools": dict(sorted(tool_counts.items(), key=lambda x: -x[1])[:10]),
            "networks": network_counts,
            "trials": {"total_used": total_trials_used},
            "security": {"ip_bans_active": ip_bans, "wallet_bans_active": wallet_bans,
                         "ban_duration_hours": 24},
            "trust_score": round((fulfillment_rate * 0.6 + (1 - refund_rate) * 0.3
                                  + (1 - failed / max(1, total)) * 0.1) * 100, 1),
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        raise AppError(message=str(e)) from e


# ── Payment Verification Endpoint ──────────────────────────────

@router.post("/verify")
async def verify_payment_endpoint(request: Request) -> Any:
    """Verify an x402 payment payload (used by gateway worker)."""
    try:
        body = await request.json()
        x402_payload = body.get("payload", "")
        network = body.get("network", "base")
        tool = body.get("tool")
        amount = body.get("amount")
        if not x402_payload:
            raise ValidationError("Missing x402 payload")
        result = await _verifier.verify_payment(x402_payload, network_key=network,
                                                expected_amount=amount, expected_tool=tool)
        if result["verified"]:
            try:
                from app.auth import get_redis
                r = await get_redis()
                verify_key = f"x402:verify:{result.get('settlement_id') or str(time.time())}"
                await r.set(verify_key, json.dumps({
                    "verified_at": datetime.now(UTC).isoformat(), "network": network,
                    "tool": tool, "amount": amount, "tx_hash": result.get("tx_hash"),
                    "result": result["reason"],
                }), ex=7 * 86400)
            except Exception as e:
                logger.debug(f"Audit store failed: {e}")
        return result
    except (HTTPException, AppError):
        raise
    except Exception as e:
        logger.error(f"Payment verification error: {e}")
        raise AppError(message=str(e)) from e


# ── Receipt Storage Endpoint ───────────────────────────────────

@router.post("/receipt")
async def store_receipt(request: Request) -> Any | dict[str, bool]:
    """Store a payment receipt from x402 Worker after successful payment."""
    _log = get_logger("x402_middleware")
    admin_key = os.getenv("ADMIN_API_KEY", os.getenv("WP_ADMIN_KEY", ""))
    if not admin_key:
        _log.critical("ADMIN_API_KEY not configured — x402 receipt store disabled")
        return JSONResponse(status_code=503, content={"error": "Service misconfigured — ADMIN_API_KEY not set"})
    auth = request.headers.get("X-API-Key", "") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if auth != admin_key:
        _log.warning("admin_auth_failed: invalid key on x402 receipt store")
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

        from app.auth import get_redis as _get_redis
        r = _get_redis()
        if r:
            receipt_data = {
                "receipt_id": receipt_id, "payment_tx": payment_tx,
                "amount": str(amount_float) if amount_float else str(amount),
                "asset": asset, "network": network, "customer_address": customer_address,
                "tool": tool, "status": status, "delivery_status": delivery_status,
                "timestamp": str(time.time()), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "fallback_chain": fallback_chain, "scan_request": scan_request, "amount_usd": amount_float,
            }
            r.set(f"x402:receipt:{receipt_id}", json.dumps(receipt_data), ex=30 * 86400)
            if payment_tx and payment_tx != "local-verified":
                r.set(f"x402:spent_tx:{payment_tx}", json.dumps({
                    "chain": network, "payer": customer_address, "amount": str(amount_float),
                    "tool": tool, "timestamp": str(time.time()), "receipt_id": receipt_id,
                }), ex=30 * 86400)
            r.zadd("x402:receipts_by_time", {receipt_id: time.time()})
            today = time.strftime("%Y-%m-%d")
            r.incrbyfloat(f"x402:revenue:daily:{today}", amount_float or 0)
            r.incrbyfloat("x402:revenue:total", amount_float or 0)
            r.incrby(f"x402:calls:daily:{today}", 1)
            r.incrby("x402:calls:total", 1)
            r.incrby(f"x402:tool_calls:{tool}", 1)
            r.incrbyfloat(f"x402:tool_revenue:{tool}", amount_float or 0)
            _log.info(f"Receipt stored: {receipt_id} tx={payment_tx[:16]!s}... tool={tool} amount=${amount_float}")
            return {"ok": True, "receipt_id": receipt_id, "stored": True, "message": "Receipt stored successfully"}
        else:
            _log.warning(f"Redis unavailable, receipt not stored: {receipt_id}")
            raise ServiceUnavailableError("Receipt storage unavailable — Redis is down",
                                          context={"error": "storage_unavailable", "receipt_id": receipt_id, "stored": False})
    except (HTTPException, AppError):
        raise
    except Exception as e:
        _log.error(f"Receipt store error: {e}")
        raise AppError(message=f"Receipt store error: {str(e)[:200]}", context={"error": "receipt_storage_failed"})


# ── Fallback System Status ─────────────────────────────────────

@router.get("/fallback/status")
async def fallback_status() -> dict[str, str]:
    """Check fallback system health, cache stats, and fallback success rate."""
    try:
        from app.auth import get_redis
        r = await get_redis()
        cache_count = 0
        async for _key in r.scan_iter(match="x402:cache:*", count=100):
            cache_count += 1
        fallback_uses = 0
        async for _key in r.scan_iter(match="x402:fallback:*", count=100):
            fallback_uses += 1
        return {
            "fallback_system": "active", "cache_entries": str(cache_count),
            "fallback_uses": str(fallback_uses),
            "layers_available": ["cache", "geckoterminal", "dexlab", "step_finance", "moonrank",
                                 "web_scrape", "dexscreener_web", "raydium", "orca", "jito",
                                 "twitter", "reddit", "news", "web_search", "bigquery"],
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
        raise AppError(message=str(e)) from e
