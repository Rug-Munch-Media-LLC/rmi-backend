"""Admin & discovery endpoints for x402 payment system.

Extracted from enforcement.py (P6 - complexity refactor).

Provides APIRouters for:
- /.well-known/x402 discovery
- /api/v1/x402/trial-status
- /api/v1/x402/revenue
- /api/v1/x402/analytics/pricing-suggestions
- /api/v1/x402/refund
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.domains.billing.x402.enforcement_action import SECURITY_HEADERS
from app.domains.billing.x402.pricing_catalog import (
    CHAIN_NAMES,
    CHAIN_USDC,
    EVM_PAY_TO,
    SOL_PAY_TO,
    TOOL_PRICES,
    _build_extra,
    _ensure_pay_addresses,
    _ensure_tool_prices,
    _resolve_asset,
    _resolve_pay_to,
)
from app.routers.protection import get_client_id

logger = get_logger("x402_enforcement")

discovery_router = APIRouter()
router = APIRouter(prefix="/api/v1/x402", tags=["x402 Enforcement"])

_discovery_cache: dict[str, Any] | None = None
_discovery_cache_time: float = 0
DISCOVERY_CACHE_TTL = 300


def _build_discovery_response() -> dict[str, Any]:
    """Build the full discovery response with all 13 chains and active facilitators."""
    _ensure_tool_prices()
    _ensure_pay_addresses()
    tools: dict[str, Any] = {}
    for tool_id, pricing in TOOL_PRICES.items():
        try:
            price_atoms = str(pricing.get("price_atoms", "10000"))
            price_usd = float(pricing.get("price_usd", 0.01))
            trial_free = int(pricing.get("trial_free", 1))
            category = pricing.get("category", "analysis")
        except (ValueError, TypeError):
            continue

        requirements: list[dict[str, Any]] = []
        for chain_key, cfg in CHAIN_USDC.items():
            method = cfg["method"]
            pay_to = _resolve_pay_to(method)
            asset = _resolve_asset(cfg)
            extra = _build_extra(cfg, tool_id, chain_key, pay_to, method)

            req: dict[str, Any] = {
                "scheme": "exact",
                "network": cfg["network"],
                "asset": asset,
                "amount": price_atoms,
                "payTo": pay_to,
                "maxTimeoutSeconds": 180,
                "extra": extra,
                "facilitators": cfg.get("facilitators", []),
            }
            if "tokens" in cfg:
                req["supportedTokens"] = list(cfg["tokens"].keys())
            requirements.append(req)

        desc = pricing.get("description", "")
        if not desc:
            _TOOL_DESCRIPTIONS: dict[str, str] = {
                "forensic_valuation": "Institutional-grade token valuation - DCF intrinsic value, comparable analysis with outlier detection, scam probability scoring",
                "osint_identity_hunt": "Cross-platform OSINT - hunt usernames across 400+ networks, domain intelligence, stealth page capture",
                "investigation_report": "Full investigation report - on-chain forensics, financial valuation, OSINT, scam scoring in one deliverable",
                "forensic_pack": "Forensic Investigation Pack - valuation + OSINT + report at 33% discount",
            }
            desc = _TOOL_DESCRIPTIONS.get(tool_id, f"{tool_id} - crypto intelligence tool")
        tools[tool_id] = {
            "description": desc,
            "price_usd": price_usd,
            "category": category,
            "trial_free": trial_free,
            "trial_description": f"{trial_free} free calls before payment required",
            "requirements": requirements,
        }

    supported_networks: list[dict[str, Any]] = []
    for ck, cv in CHAIN_USDC.items():
        net: dict[str, Any] = {
            "network": cv["network"],
            "name": CHAIN_NAMES.get(ck, ck),
            "chainId": cv.get("chain_id"),
            "currency": cv.get("name", "USD Coin"),
            "facilitators": cv.get("facilitators", []),
        }
        if "tokens" in cv:
            net["tokens"] = dict(cv["tokens"].items())
        supported_networks.append(net)

    facilitator_map: dict[str, dict[str, str]] = {
        "coinbase_cdp": {
            "name": "Coinbase CDP",
            "description": "Fee-free Base + Solana USDC via Coinbase Developer Platform",
            "url": "https://cdp.coinbase.com",
        },
        "payai": {
            "name": "PayAI",
            "description": "Base + Solana USDC, deferred settlement",
            "url": "https://payai.network",
        },
        "primev": {
            "name": "Primev",
            "description": "Fee-free Ethereum via mev-commit preconfirmations",
            "url": "https://primev.xyz",
        },
        "cloudflare_x402": {
            "name": "Cloudflare x402",
            "description": "Base Sepolia + Ethereum fallback",
            "url": "https://x402.org",
        },
        "eip7702": {
            "name": "EIP-7702 Universal",
            "description": "Universal EVM - BSC, Polygon, Avalanche, Fantom, Gnosis, Arbitrum, Optimism, Base",
            "url": "https://eips.ethereum.org/EIPS/eip-7702",
        },
        "tron_selfverify": {
            "name": "TRON Self-Verify",
            "description": "TRON USDT/USDC/USDD - self-verified via TronGrid (fee-free)",
            "url": "https://trongrid.io",
        },
        "bitcoin_selfverify": {
            "name": "Bitcoin Self-Verify",
            "description": "Bitcoin BTC - self-verified via Mempool.space (fee-free, 1-conf)",
            "url": "https://mempool.space",
        },
        "asterpay": {
            "name": "AsterPay",
            "description": "EUR/SEPA European off-ramp",
            "url": "https://asterpay.io",
        },
    }
    payment_facilitators: list[dict[str, Any]] = []
    for fk, fv in facilitator_map.items():
        fac_networks = [n["network"] for n in supported_networks if fk in n.get("facilitators", [])]
        payment_facilitators.append(
            {
                "name": fv["name"],
                "description": fv["description"],
                "url": fv["url"],
                "supportedNetworks": fac_networks,
            }
        )

    return {
        "x402": {
            "version": "2",
            "protocol": "x402",
            "description": "Rug Munch Intelligence (RMI) - Multi-chain x402 payment system. Crypto scam detection, market analysis, and security intelligence via micropayments.",
            "trial_policy": "1 free trial per tool without wallet. Connect wallet for 3 free calls per standard tool, 1 per premium tool.",
            "refund_policy": "Full refund if tool returns no data. Request within 48h via POST /api/v1/x402/refund with tx hash.",
            "facilitator_summary": {
                "coinbase_cdp": "Fee-free Base + Solana USDC via Coinbase Developer Platform",
                "payai": "Base + Solana USDC, deferred settlement",
                "primev": "Fee-free Ethereum via mev-commit preconfirmations",
                "cloudflare_x402": "Base Sepolia + Ethereum fallback",
                "eip7702": "Universal EVM - BSC, Polygon, Avalanche, Fantom, Gnosis, Arbitrum, Optimism, Base",
                "tron_selfverify": "TRON USDT/USDC/USDD - self-verified via TronGrid (fee-free)",
                "bitcoin_selfverify": "Bitcoin BTC - self-verified via Mempool.space (fee-free, 1-conf)",
                "asterpay": "EUR/SEPA European off-ramp",
                "x402_rs": "Self-hosted x402-rs - multi-chain (requires Docker)",
            },
        },
        "gateway_url": "https://mcp.rugmunch.io",
        "payment_endpoint": "https://mcp.rugmunch.io/api/v1/x402-tools",
        "supported_chains": list(CHAIN_USDC.keys()),
        "supportedNetworks": supported_networks,
        "paymentFacilitators": payment_facilitators,
        "chain_count": len(CHAIN_USDC),
        "facilitator_count": len(payment_facilitators),
        "total_tools": len(tools),
        "tools": tools,
    }


@discovery_router.get("/.well-known/x402")
async def x402_discovery() -> dict[str, Any]:
    """x402 discovery endpoint - lists all tools, chains, payment requirements, and trial info."""
    global _discovery_cache, _discovery_cache_time

    now = time.time()
    if _discovery_cache is not None and (now - _discovery_cache_time) < DISCOVERY_CACHE_TTL:
        return _discovery_cache

    _discovery_cache = _build_discovery_response()
    _discovery_cache_time = now
    return _discovery_cache


@discovery_router.get("/api/v1/x402/trial-status")
async def get_trial_status(request: Request) -> dict[str, Any]:
    """Check remaining free trials for the current client."""
    client_id = get_client_id(request)
    trials: dict[str, Any] = {}
    for tool_id, pricing in TOOL_PRICES.items():
        max_trials = pricing.get("trial_free", 3)
        if max_trials > 0:
            r = get_redis()
            if r:
                try:
                    used = int(r.get(f"x402:trial:{client_id}:{tool_id}") or 0)
                except Exception:
                    used = 0
            else:
                used = 0
            trials[tool_id] = {
                "max": max_trials,
                "used": used,
                "remaining": max(0, max_trials - used),
            }

    return {
        "client_id": client_id[:20] + "..." if len(client_id) > 20 else client_id,
        "trials": trials,
        "tools_with_trials": len(trials),
    }


@discovery_router.get("/api/v1/x402/revenue", response_model=None)
async def get_revenue(request: Any = None) -> JSONResponse:
    """Read cumulative x402 revenue stats from Redis counters."""
    _ensure_pay_addresses()
    if request:
        admin_key = os.getenv("ADMIN_API_KEY", "")
        if not admin_key:
            logger.critical("ADMIN_API_KEY not configured — revenue endpoint disabled")
            return JSONResponse(
                status_code=503, content={"error": "Service misconfigured — ADMIN_API_KEY not set"}
            )
        auth = request.headers.get("X-API-Key", "") or request.headers.get(
            "Authorization", ""
        ).replace("Bearer ", "")
        if auth != admin_key:
            logger.warning("admin_auth_failed: invalid key on /revenue")
            return JSONResponse(status_code=403, content={"error": "Admin API key required"})
    r = get_redis()
    if not r:
        return JSONResponse(
            status_code=503,
            content={"error": "Redis unavailable, cannot read revenue"},
            headers=SECURITY_HEADERS,
        )

    try:
        total_revenue = float(r.get("x402:revenue:total") or 0)
        total_calls = 0
        tool_revenue: dict[str, float] = {}
        tool_calls: dict[str, int] = {}

        for key in r.scan_iter("x402:tool_revenue:*"):
            tool_id = key.decode().replace("x402:tool_revenue:", "")
            tool_revenue[tool_id] = float(r.get(key) or 0)

        for key in r.scan_iter("x402:tool_calls:*"):
            tool_id = key.decode().replace("x402:tool_calls:", "")
            count = int(r.get(key) or 0)
            tool_calls[tool_id] = count
            total_calls += count

        daily: dict[str, float] = {}
        for i in range(30):
            day = (datetime.now(UTC) - timedelta(days=i)).strftime("%Y-%m-%d")
            val = float(r.get(f"x402:revenue:daily:{day}") or 0)
            if val > 0:
                daily[day] = val

        return JSONResponse(
            content={
                "total_revenue_usd": round(total_revenue, 2),
                "total_tool_calls": total_calls,
                "by_tool": {
                    tid: {
                        "revenue_usd": round(tool_revenue.get(tid, 0), 2),
                        "calls": tool_calls.get(tid, 0),
                    }
                    for tid in set(list(tool_revenue.keys()) + list(tool_calls.keys()))
                },
                "daily": daily,
                "payment_wallets": {
                    "evm": EVM_PAY_TO,
                    "solana": SOL_PAY_TO,
                    "tron": os.getenv("X402_TRON_PAY_TO", ""),
                    "bitcoin": os.getenv("X402_BTC_PAY_TO", ""),
                },
            }
        )
    except Exception as e:
        logger.error(f"Revenue read error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to read revenue: {str(e)[:100]}"},
            headers=SECURITY_HEADERS,
        )


@discovery_router.get("/api/v1/x402/analytics/pricing-suggestions")
async def get_pricing_suggestions() -> JSONResponse:
    """Auto-pricing suggestions based on conversion rates and usage data."""
    r = get_redis()
    if not r:
        return JSONResponse(
            status_code=503,
            content={"error": "Redis unavailable"},
            headers=SECURITY_HEADERS,
        )
    try:
        suggestions: list[dict[str, Any]] = []
        for tool_id, pricing in TOOL_PRICES.items():
            current_price = pricing.get("price_usd", 0)
            trial_free = pricing.get("trial_free", 3)

            paid_calls = int(r.get(f"x402:tool_calls:{tool_id}") or 0)
            trial_calls = int(r.get(f"x402:trial_calls:{tool_id}") or 0)
            revenue = float(r.get(f"x402:tool_revenue:{tool_id}") or 0)

            total_calls = paid_calls + trial_calls
            conversion_rate = paid_calls / total_calls if total_calls > 0 else 0
            avg_revenue_per_call = revenue / paid_calls if paid_calls > 0 else 0

            suggestion = None
            suggested_price = current_price

            if total_calls < 10:
                suggestion = "insufficient_data"
            elif total_calls > 100 and conversion_rate < 0.05:
                suggestion = "decrease_price"
                suggested_price = round(current_price * 0.6, 3)
            elif conversion_rate > 0.40:
                suggestion = "increase_price"
                suggested_price = round(current_price * 1.3, 3)
            elif total_calls > 50 and conversion_rate < 0.15:
                suggestion = "consider_decrease"
                suggested_price = round(current_price * 0.8, 3)
            elif paid_calls == 0 and trial_calls > trial_free * 10:
                suggestion = "price_barrier"
                suggested_price = round(current_price * 0.5, 3)
            elif revenue > 0 and avg_revenue_per_call < current_price * 0.5:
                suggestion = "check_pricing_consistency"

            suggestions.append(
                {
                    "tool_id": tool_id,
                    "current_price_usd": current_price,
                    "suggested_price_usd": suggested_price,
                    "suggestion": suggestion,
                    "stats": {
                        "paid_calls": paid_calls,
                        "trial_calls": trial_calls,
                        "total_calls": total_calls,
                        "conversion_rate": round(conversion_rate * 100, 1),
                        "revenue_usd": round(revenue, 4),
                        "avg_revenue_per_call": round(avg_revenue_per_call, 4),
                    },
                }
            )

        suggestions.sort(
            key=lambda s: 0 if s["suggestion"] and s["suggestion"] != "insufficient_data" else 1
        )

        return JSONResponse(
            content={
                "suggestions": suggestions,
                "total_tools": len(suggestions),
                "actionable": sum(
                    1
                    for s in suggestions
                    if s["suggestion"] and s["suggestion"] != "insufficient_data"
                ),
            }
        )
    except Exception as e:
        logger.error(f"Pricing suggestions error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)[:100]},
            headers=SECURITY_HEADERS,
        )


def _verify_refund_ownership(refund_payer: str, tx_hash: str) -> bool:
    """Verify the requester controls the payer address by checking on-chain."""
    r = get_redis()
    if not r:
        return False

    spent_data = r.get(f"x402:spent_tx:{tx_hash}")
    if not spent_data:
        return False

    try:
        spent_info = json.loads(spent_data)
        recorded_payer = spent_info.get("payer", "").lower()
        return bool(refund_payer and recorded_payer and refund_payer.lower() == recorded_payer)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return False


@router.post("/refund")
async def request_refund(request: Request) -> JSONResponse:
    """Request a refund for a paid tool that returned no data."""
    try:
        body = await request.json()
        tx_hash = body.get("tx_hash", "").strip()
        requester_payer = body.get("payer", "").strip()
        requester_reason = body.get("reason", "").strip()

        if not tx_hash:
            return JSONResponse(
                status_code=400,
                content={"error": "tx_hash is required"},
                headers=SECURITY_HEADERS,
            )

        if not requester_payer:
            return JSONResponse(
                status_code=400,
                content={"error": "payer address is required (proof of ownership)"},
                headers=SECURITY_HEADERS,
            )

        if not _verify_refund_ownership(requester_payer, tx_hash):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Proof of ownership failed. The payer address does not match our records for this transaction.",
                    "tx_hash": tx_hash[:16] + "...",
                    "note": "Provide the wallet address that originally paid for this transaction.",
                },
                headers=SECURITY_HEADERS,
            )

        r = get_redis()
        if not r:
            return JSONResponse(
                status_code=503,
                content={"error": "Redis unavailable, cannot process refund request"},
                headers=SECURITY_HEADERS,
            )

        spent_data = r.get(f"x402:spent_tx:{tx_hash}")
        existing_refund = r.get(f"x402:refund:{tx_hash}")

        if not spent_data and not existing_refund:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "Transaction not found in payment records",
                    "tx_hash": tx_hash[:16] + "...",
                },
                headers=SECURITY_HEADERS,
            )

        if existing_refund:
            try:
                refund_info = json.loads(existing_refund)
                status = refund_info.get("status", "unknown")
                if status in ("requested", "processing", "completed"):
                    return {
                        "status": "already_requested",
                        "refund_status": status,
                        "tx_hash": tx_hash[:16] + "...",
                        "message": f"Refund already {status}",
                        "amount_atoms": refund_info.get("amount_atoms"),
                        "chain": refund_info.get("chain"),
                    }
            except (json.JSONDecodeError, TypeError):
                pass

        refund_reason = requester_reason or "User-requested refund"
        refund_chain = "unknown"
        refund_payer = requester_payer or "unknown"
        refund_amount = "0"
        payment_timestamp = None

        if existing_refund:
            try:
                refund_info = json.loads(existing_refund)
                if refund_info.get("status") == "refundable":
                    refund_reason = refund_info.get("reason") or refund_reason
                    refund_chain = refund_info.get("chain", "unknown")
                    refund_payer = refund_info.get("payer", requester_payer)
                    refund_amount = refund_info.get("amount_atoms", "0")
                    payment_timestamp = refund_info.get("flagged_at")
            except (json.JSONDecodeError, TypeError):
                pass
        elif spent_data:
            try:
                spent_info = json.loads(spent_data)
                refund_chain = spent_info.get("chain", "unknown")
                refund_payer = spent_info.get("payer", requester_payer)
                refund_amount = spent_info.get("amount", "0")
                payment_timestamp = spent_info.get("timestamp")
            except (json.JSONDecodeError, TypeError):
                refund_chain = spent_data if isinstance(spent_data, str) else "unknown"

        if payment_timestamp:
            hours_since_payment = (time.time() - float(payment_timestamp)) / 3600
            if hours_since_payment > 48:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Refund window expired (48h)",
                        "hours_since_payment": round(hours_since_payment, 1),
                        "tx_hash": tx_hash[:16] + "...",
                    },
                    headers=SECURITY_HEADERS,
                )

        refund_key = f"x402:refund:{tx_hash}"
        refund_data = {
            "tx_hash": tx_hash,
            "chain": refund_chain,
            "payer": refund_payer,
            "amount_atoms": str(refund_amount),
            "tool": "unknown",
            "reason": refund_reason,
            "status": "requested",
            "flagged_at": payment_timestamp,
            "requested_at": time.time(),
            "processed_at": None,
        }
        r.setex(refund_key, 7 * 86400, json.dumps(refund_data))

        logger.info(
            f"Refund requested: tx={tx_hash[:16]}... chain={refund_chain} "
            f"amount={refund_amount} reason={refund_reason}"
        )

        return {
            "status": "requested",
            "tx_hash": tx_hash[:16] + "...",
            "chain": refund_chain,
            "amount_atoms": str(refund_amount),
            "reason": refund_reason,
            "message": "Refund request recorded. Manual processing within 48h. You will receive USDC back on the original payment chain.",
            "estimated_processing": "24-48 hours",
        }

    except Exception as e:
        logger.error(f"Refund request error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Refund request failed: {str(e)[:100]}"},
            headers=SECURITY_HEADERS,
        )
