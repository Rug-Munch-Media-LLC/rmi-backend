"""Fail-closed payment gate for the /api/v1/x402 advanced + premium tools.

Background (audit 2026-08-13, F-03): the advanced tools router
(rug_probability, narrative, history, stream/alerts, cache/*) and the
premium tools router (reputation_score, investigation_report,
webhook_register, webhook_list) were mounted at /api/v1/x402/* with NO
payment check at all. The canonical path classifier
(route_classifier.is_free_path) intentionally exempts the whole
/api/v1/x402/ prefix, so wiring the canonical ``payment_gate`` dependency
here would have NO effect — this gate is required.

Explicit decision (documented, not silent):
- DEFAULT (closed): every request must present either
    1. a valid x402 payment proof (X-PAYMENT / X-Payment header), or
    2. an explicit admin API key (X-Api-Key matching ADMIN_API_KEY /
       WP_ADMIN_KEY) for ops endpoints (cache/*, webhook_list, ...).
- Free/preview mode is an EXPLICIT opt-in: set X402_ADVANCED_TOOLS_OPEN=1
  in the environment. Do NOT enable in production. The gate NEVER trusts
  header presence: garbage or missing proofs are rejected with 402.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Request  # noqa: TC002 — FastAPI needs runtime resolution for request objects

from app.core.errors import PaymentRequiredError
from app.core.logging import get_logger

logger = get_logger("x402.advanced_gate")

# Explicit, documented preview-mode constant. Default: closed (payment or
# admin API key required). Read once at import; tests may monkeypatch it.
X402_ADVANCED_TOOLS_OPEN = os.getenv("X402_ADVANCED_TOOLS_OPEN", "0") == "1"

# Fallback per-call price (USD) for tool ids without a pricing entry.
_ADVANCED_FALLBACK_PRICE_USD = 0.05


def _tool_id_for_path(path: str) -> str:
    """Derive a stable tool id from the request path (last segment)."""
    return path.rstrip("/").split("/")[-1] or "unknown"


def _price_for_tool(tool_id: str) -> float:
    from app.routers.x402_databus_tools.pricing import X402_TOOL_PRICING

    entry = X402_TOOL_PRICING.get(tool_id, {})
    return float(entry.get("price_usd", _ADVANCED_FALLBACK_PRICE_USD))


async def require_x402_advanced_payment(request: Request) -> dict[str, Any]:
    """FastAPI dependency — fail-closed payment gate for advanced/premium tools.

    Raises ``PaymentRequiredError`` (402) for missing/garbage payment
    proofs. Returns a small context dict on success.
    """
    tool_id = _tool_id_for_path(request.url.path)

    if X402_ADVANCED_TOOLS_OPEN:
        # Documented preview mode (X402_ADVANCED_TOOLS_OPEN=1). Log loudly so
        # a misconfigured prod box is obvious.
        logger.warning("x402 advanced tools served FREE: X402_ADVANCED_TOOLS_OPEN=1 (preview mode)")
        return {"tier": "preview", "tool": tool_id, "paid_via_x402": None}

    # Explicit admin API key auth (ops endpoints: cache/*, webhook_list, ...).
    api_key = request.headers.get("X-Api-Key", "")
    admin_key = os.getenv("ADMIN_API_KEY", os.getenv("WP_ADMIN_KEY", ""))
    if admin_key and api_key and hmac.compare_digest(api_key, admin_key):
        return {"tier": "admin", "tool": tool_id, "paid_via_x402": None}

    # Otherwise require a valid x402 payment proof. Missing header -> 402.
    if not (request.headers.get("X-PAYMENT", "") or request.headers.get("X-Payment", "")):
        raise PaymentRequiredError(
            "payment_required",
            context={
                "tool_id": tool_id,
                "price_usd": _price_for_tool(tool_id),
                "payment_required": {
                    "x402_version": 1,
                    "max_amount": f"{_price_for_tool(tool_id):.4f}",
                    "payment_type": "USDC",
                    "networks": ["base", "solana", "ethereum"],
                },
            },
        )

    try:
        from app.routers.x402_databus_tools.auth import verify_x402_payment

        payment = await verify_x402_payment(request, tool_id, _price_for_tool(tool_id))
        if not payment.get("paid"):
            raise PaymentRequiredError(
                "payment_verification_failed",
                context={"tool_id": tool_id, "reason": "payment not confirmed"},
            )
        return {"tier": "paid", "tool": tool_id, "paid_via_x402": payment.get("tx", "")}
    except PaymentRequiredError:
        raise
    except Exception as e:  # noqa: BLE001, RUF100 — fail closed on any verifier error
        logger.warning(f"x402 advanced gate verification error for tool={tool_id}: {e}")
        raise PaymentRequiredError(
            "payment_verification_failed",
            context={"tool_id": tool_id, "error": str(e)[:200]},
        ) from e


__all__ = ["X402_ADVANCED_TOOLS_OPEN", "require_x402_advanced_payment"]
