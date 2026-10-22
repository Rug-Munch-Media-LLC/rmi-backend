"""x402 exact — HTTP 402 challenge builder.

Emits a spec-shaped challenge: ``accepts[]`` carries the scheme, network,
asset, payTo, maxAmountRequired, resource and a server-issued nonce (the
payment id the caller must echo back in X-PAYMENT).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from app.domains.billing.x402_exact.catalog import (
    NETWORK_BASE,
    USDC_BASE,
    PaidEndpoint,
)

DEFAULT_EVM_PAY_TO = "0x1E3AC01d0fdb976179790BDD02823196A92705C9"


def _pay_to() -> str:
    """Resolve the pay-to address from env, never hardcoded in a response."""
    return (
        os.getenv("X402_PAYMENT_ADDRESS_EVM")
        or os.getenv("X402_EVM_PAY_TO")
        or os.getenv("X402_PAYMENT_ADDRESS")
        or DEFAULT_EVM_PAY_TO
    )


def _public_base() -> str:
    """Absolute base URL used for the resource field."""
    return os.getenv("X402_PUBLIC_BASE_URL", "https://api.rugmunch.io").rstrip("/")


def build_challenge(
    endpoint: PaidEndpoint,
    *,
    error: str = "payment_required",
    detail: str | None = None,
) -> dict[str, Any]:
    """Return the 402 body for an unpaid (or under-paid) call."""
    nonce = uuid.uuid4().hex
    resource = f"{_public_base()}{endpoint.path}"
    body: dict[str, Any] = {
        "x402Version": 1,
        "error": error,
        "accepts": [
            {
                "scheme": "exact",
                "network": NETWORK_BASE,
                "maxAmountRequired": str(endpoint.price_atoms),
                "resource": resource,
                "description": endpoint.description,
                "mimeType": "application/json",
                "payTo": _pay_to(),
                "asset": USDC_BASE,
                "maxTimeoutSeconds": 300,
                "extra": {
                    "name": "USD Coin",
                    "version": "2",
                    "decimals": 6,
                    "nonce": nonce,
                    "facilitator": os.getenv("X402_EXACT_FACILITATOR", "coffer"),
                },
            }
        ],
        "freeTier": {
            "callsPerDay": endpoint.free_daily,
            "exhausted": True,
            "upgrade": "settle one call to continue without the daily cap",
        },
        "clientInstructions": (
            "Transfer USDC to payTo on Base, then retry with "
            "X-PAYMENT: base64(json({x402Version, paymentId, txHash, network, asset, amountUsd}))"
        ),
    }
    if detail:
        body["detail"] = detail
    return body
