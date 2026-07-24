"""Enforcement action — builds x402 responses for enforcement decisions.

Extracted from enforcement.py (P6 - complexity refactor).

Responsibility:
- Build 402 Payment Required response (v2 spec compliant)
- Build 403/429/400 error responses
- Build v2 spec accepts list, resource info, bazaar extension
- Record refundable payments in Redis
"""

from __future__ import annotations

import base64 as _base64
import json
import os
import time
from typing import Any

from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.domains.billing.x402.pricing_catalog import (
    CHAIN_NAMES,
    CHAIN_USDC,
    EVM_PAY_TO,
    SOL_PAY_TO,
    TOOL_PRICES,
    _ensure_pay_addresses,
)
from app.domains.billing.x402.tier_resolver import check_trial

logger = get_logger("x402_enforcement")

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}

def _build_accepts_list(tool_id: str, pricing: dict[str, Any]) -> list[dict[str, Any]]:
    """Build x402 v2 spec 'accepts' array from TOOL_PRICES and CHAIN_USDC."""
    _ensure_pay_addresses()
    accepts: list[dict[str, Any]] = []
    for chain_key, cfg in CHAIN_USDC.items():
        method = cfg["method"]

        if method == "payai":
            pay_to = SOL_PAY_TO
        elif method == "bitcoin_selfverify":
            pay_to = os.getenv("X402_BTC_PAY_TO", "")
        elif method == "tron_selfverify":
            pay_to = os.getenv("X402_TRON_PAY_TO", "")
        elif method == "asterpay":
            pay_to = os.getenv("ASTERPAY_SEPA_IBAN", "")
        else:
            pay_to = EVM_PAY_TO

        asset = cfg.get("usdc", "")
        if not asset and "tokens" in cfg:
            first_token = next(iter(cfg["tokens"].values()), "")
            asset = first_token if first_token != "native" else ""

        extra: dict[str, Any] = {
            "name": cfg["name"],
            "version": cfg["version"],
            "tool": tool_id,
            "chain": chain_key,
        }

        if method == "local_eip712" and cfg.get("chain_id"):
            extra["domain"] = {
                "name": cfg["name"],
                "version": cfg["version"],
                "chainId": cfg["chain_id"],
                "verifyingContract": pay_to,
            }
        elif method == "payai":
            extra["feePayer"] = "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4"
        elif method == "tron_selfverify":
            extra["tronNetwork"] = "mainnet"
            extra["trc20Tokens"] = cfg.get("tokens", {})
        elif method == "bitcoin_selfverify":
            extra["paymentNetwork"] = "bitcoin"
            extra["settlementChains"] = ["base", "solana"]
        elif method == "asterpay":
            extra["currency"] = "EUR"
            extra["sepa"] = True

        requirement: dict[str, Any] = {
            "scheme": "exact",
            "network": cfg["network"],
            "asset": asset,
            "amount": pricing["price_atoms"],
            "payTo": pay_to,
            "maxTimeoutSeconds": 180,
            "extra": extra,
        }

        if "tokens" in cfg:
            requirement["supportedTokens"] = list(cfg["tokens"].keys())

        accepts.append(requirement)

    return accepts

def _build_bazaar_extension(
    tool_id: str, pricing: dict[str, Any], accepts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build x402 v2 bazaar extension for facilitator discovery indexing."""
    CATEGORY_MAP: dict[str, str] = {
        "security": "security",
        "intelligence": "intelligence",
        "market": "market-data",
        "analysis": "analytics",
        "social": "social",
        "launchpad": "launchpad",
        "bundle": "bundles",
        "defi": "defi",
        "premium": "premium",
        "api": "api",
        "variant": "crypto",
    }
    category = pricing.get("category", "analysis")
    bazaar_category = CATEGORY_MAP.get(category, category)

    read_only_suffixes = ("_info", "_list", "_check", "_scan", "_status", "_health")
    is_read_only = tool_id.endswith(read_only_suffixes)
    http_method = "GET" if is_read_only else "POST"

    first_accept = accepts[0] if accepts else {}
    first_accept.get("extra", {})

    info_input: dict[str, Any] = {
        "type": "http",
        "method": http_method,
    }

    if http_method == "GET":
        info_input["queryParams"] = {
            "address": {
                "type": "string",
                "description": f"Blockchain address or identifier for {tool_id}",
            },
            "chain": {"type": "string", "description": "Blockchain to query (default: base)"},
        }
    else:
        info_input["bodyType"] = "json"
        info_input["body"] = {
            "address": {
                "type": "string",
                "description": f"Blockchain address or identifier for {tool_id}",
            },
            "chain": {"type": "string", "description": "Blockchain to query (default: base)"},
        }

    info: dict[str, Any] = {
        "input": info_input,
        "output": {
            "type": "json",
            "example": {
                "data": {"result": "tool output"},
                "sources_used": ["source1", "source2"],
            },
        },
        "category": bazaar_category,
        "tags": ["crypto", "security", "blockchain", tool_id],
        "description": pricing.get("description", f"{tool_id} - Rug Munch Intelligence"),
    }

    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "input": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "const": "http"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "HEAD", "DELETE"]
                        if http_method == "GET"
                        else ["POST", "PUT", "PATCH"],
                    },
                    "queryParams": {"type": "object"},
                    "bodyType": {"type": "string", "enum": ["json", "form-data", "text"]},
                    "body": {"type": "object"},
                },
                "required": ["type", "method"],
                "additionalProperties": False,
            },
            "output": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "example": {"type": "object"},
                },
                "required": ["type"],
            },
            "category": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
        },
        "required": ["input"],
    }

    return {"info": info, "schema": schema}

def _build_resource_info(tool_id: str, pricing: dict[str, Any]) -> dict[str, Any]:
    """Build x402 v2 ResourceInfo object for PaymentRequired response."""
    description = pricing.get("description", f"Rug Munch Intelligence - {tool_id}")
    if len(description) > 200:
        description = description[:197] + "..."

    return {
        "url": f"https://mcp.rugmunch.io/api/v1/x402-tools/{tool_id}",
        "description": description,
        "mimeType": "application/json",
    }

def build_402_response(tool_id: str, client_id: str = "") -> JSONResponse:
    """Build an x402 v2 compliant Payment Required response."""
    pricing = TOOL_PRICES.get(tool_id, {"price_usd": 0.01, "price_atoms": "10000", "trial_free": 3})
    trial_free = pricing.get("trial_free", 3)

    remaining = 0
    if client_id:
        _can_use, remaining = check_trial(tool_id, client_id)

    _ensure_pay_addresses()
    accepts = _build_accepts_list(tool_id, pricing)
    resource = _build_resource_info(tool_id, pricing)
    bazaar = _build_bazaar_extension(tool_id, pricing, accepts)

    v2_body: dict[str, Any] = {
        "x402Version": 2,
        "error": "PAYMENT-SIGNATURE header is required",
        "resource": resource,
        "accepts": accepts,
        "extensions": {"bazaar": bazaar},
        "error_legacy": "Payment Required",
        "tool": tool_id,
        "price": f"${pricing['price_usd']:.2f}",
        "trial_free": trial_free,
        "trial_remaining": remaining,
        "trial_used": remaining <= 0,
        "wallet_required": remaining < 0,
        "message": (
            f"Connect a wallet to continue. Your 1 free trial is used - link MetaMask or Phantom to get {trial_free} free calls per tool. From ${pricing['price_usd']:.2f}/call after that."
            if remaining < 0
            else f"All {trial_free} free trial{'s' if trial_free != 1 else ''} used. Pay {pricing['price_atoms']} atoms to use {tool_id}. Pay on {', '.join(CHAIN_NAMES.get(c, c) for c in CHAIN_USDC)}."
        ),
        "accepted_chains": list(CHAIN_USDC.keys()),
        "chain_details": {
            k: {
                "network": v["network"],
                "facilitators": v.get("facilitators", []),
                "tokens": list(v.get("tokens", {"USDC": v.get("usdc", "")}).keys()),
            }
            for k, v in CHAIN_USDC.items()
        },
        "x402": {
            "version": "2",
            "requirements": accepts,
        },
    }

    payment_required_header_obj = {
        "x402Version": 2,
        "error": "PAYMENT-SIGNATURE header is required",
        "resource": resource,
        "accepts": accepts,
        "extensions": {"bazaar": bazaar},
    }
    payment_required_b64 = _base64.b64encode(
        json.dumps(payment_required_header_obj, separators=(",", ":")).encode()
    ).decode("ascii")

    return JSONResponse(
        status_code=402,
        content=v2_body,
        headers={
            "PAYMENT-REQUIRED": payment_required_b64,
            "X-Paywall-Version": "2",
            "Content-Type": "application/json",
            **SECURITY_HEADERS,
        },
    )

def build_scanner_response() -> JSONResponse:
    """403 for known scanner/bot user agents."""
    return JSONResponse(
        status_code=403,
        content={
            "error": "Automated access requires x402 payment. Use x-pay header or a proper API client.",
            "docs": "https://rugmunch.io/mcp-docs",
        },
        headers=SECURITY_HEADERS,
    )

def build_burst_response(retry_after: int = 60) -> JSONResponse:
    """429 for burst rate limit exceeded."""
    BURST_LIMIT = int(os.getenv("X402_BURST_LIMIT", "5"))
    return JSONResponse(
        status_code=429,
        content={
            "error": f"Rate limited. Max {BURST_LIMIT} requests per {retry_after}s per tool.",
            "retry_after": retry_after,
        },
        headers={**SECURITY_HEADERS, "Retry-After": str(retry_after)},
    )

def build_invalid_tool_response() -> JSONResponse:
    """400 for invalid tool identifiers."""
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid tool identifier"},
        headers=SECURITY_HEADERS,
    )

def build_oversized_header_response() -> JSONResponse:
    """400 for oversized payment headers."""
    return JSONResponse(
        status_code=400,
        content={"error": "Payment header too large"},
        headers=SECURITY_HEADERS,
    )

def _record_refundable_payment(
    tx_hash: str, chain: str, payer: str, amount: str, tool: str, reason: str
) -> None:
    """Record a refundable payment in Redis when a tool returns empty data."""
    r = get_redis()
    if not r:
        logger.warning(f"Cannot record refund for {tx_hash}: Redis unavailable")
        return

    refund_key = f"x402:refund:{tx_hash}"
    refund_data = {
        "tx_hash": tx_hash,
        "chain": chain or "unknown",
        "payer": payer or "unknown",
        "amount_atoms": str(amount) if amount else "0",
        "tool": tool or "unknown",
        "reason": reason,
        "status": "refundable",
        "flagged_at": time.time(),
        "requested_at": None,
        "processed_at": None,
    }
    try:
        r.setex(refund_key, 7 * 86400, json.dumps(refund_data))
        logger.info(f"Recorded refundable payment: {tx_hash[:16]}... tool={tool} reason={reason}")
    except (json.JSONDecodeError, KeyError, TypeError) as e :
        logger.debug("_record_refundable_payment_error", exc_info=True)
        logger.error(f"Failed to record refund for {tx_hash}: {e}")
