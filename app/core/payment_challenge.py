"""Complete x402 challenge for every 402 we emit.

Before this module a ``PaymentRequiredError`` reached the client as bare
RFC-9457 (``{"type": "/errors/payment/required", "code": "payment.required"}``)
with no payment requirements — the website's paid-intel panel could see the
402 but had nothing to pay. The requirement objects were only built on the
``x402_exact`` sell routes.

``payment_challenge`` derives the same spec-shaped requirement (scheme,
network, asset, payTo, maxAmountRequired, resource, nonce) for ANY 402 from
whatever the raising gate put in ``exc.context``, and returns it both as body
fields (``x402Version``/``accepts``) and as a base64 ``PAYMENT-REQUIRED``
header, which is what x402 clients read first.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import Request  # noqa: TC002 — runtime request object

from app.domains.billing.x402_exact.challenge import build_accepts

_DEFAULT_ATOMS = 50_000  # $0.05 fallback for tool ids without a price


def _atoms(context: dict[str, Any]) -> int:
    """Price in USDC atoms from the gate context (never a float on the wire)."""
    raw = context.get("price_atoms")
    if raw is not None:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    usd = context.get("price_usd", context.get("max_amount"))
    try:
        return max(1, round(float(usd) * 1_000_000))
    except (TypeError, ValueError):
        return _DEFAULT_ATOMS


def _free_daily(context: dict[str, Any]) -> int:
    """Free calls per day the gate advertised (0 when unknown)."""
    raw = context.get("free_daily")
    return raw if isinstance(raw, int) else 0


def payment_challenge(request: Request, exc: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Return ``(body_extra, headers)`` describing how to pay for this 402."""
    context: dict[str, Any] = getattr(exc, "context", None) or {}
    tool_id = str(context.get("tool_id") or request.url.path.rstrip("/").split("/")[-1] or "resource")
    accept = build_accepts(
        path=request.url.path,
        price_atoms=_atoms(context),
        description=str(context.get("description") or f"RMI paid intel: {tool_id}"),
        free_daily=_free_daily(context),
    )
    body_extra: dict[str, Any] = {
        "x402Version": 1,
        "error": str(context.get("error") or "payment_required"),
        "accepts": [accept],
        "tool_id": tool_id,
    }
    header_payload = json.dumps({"x402Version": 1, "accepts": [accept]}, separators=(",", ":")).encode()
    headers = {
        "PAYMENT-REQUIRED": base64.b64encode(header_payload).decode(),
        "X-Payment-Required": base64.b64encode(header_payload).decode(),
    }
    return body_extra, headers


__all__ = ["payment_challenge"]
