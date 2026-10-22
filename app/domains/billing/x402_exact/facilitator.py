"""x402 exact — facilitator client (verify + settle).

Talks to a facilitator over HTTP. Default target is our own Coffer
(``http://127.0.0.1:8001``); any x402 facilitator exposing
``/v1/x402/verify`` and ``/v1/x402/settle`` works.

Settlement is only reported successful when the facilitator says so. No
local shortcut, no offline mode: an unverified payment is never served.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_FACILITATOR = "http://127.0.0.1:8001"
_TIMEOUT = 20.0


def _base_url() -> str:
    """Facilitator base URL, trailing slash stripped."""
    return (os.getenv("X402_FACILITATOR_URL") or DEFAULT_FACILITATOR).rstrip("/")


def _payload(payment_id: str, tx_hash: str, network: str, asset: str, amount_usd: float) -> dict[str, Any]:
    """Wire body shared by verify and settle."""
    return {
        "payment_id": payment_id,
        "tx_hash": tx_hash,
        "network": network,
        "asset": asset,
        "amount_usd": amount_usd,
    }


async def _post(step: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST one facilitator step and normalise the outcome."""
    url = f"{_base_url()}/v1/x402/{step}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=body)
    except (httpx.HTTPError, OSError) as exc:
        log.error(f"x402_exact_facilitator_{step}_unreachable: {exc}")
        return {"ok": False, "reason": f"facilitator_unreachable:{type(exc).__name__}"}

    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200:
        reason = str(data.get("detail") or data.get("error") or f"http_{response.status_code}")
        return {"ok": False, "reason": reason[:300], "status": response.status_code}

    ok = bool(data.get("verified") or data.get("settled") or data.get("success"))
    tx_hash = data.get("tx_hash") or data.get("transactionHash") or data.get("txHash") or ""
    return {"ok": ok, "reason": str(data.get("reason") or ""), "tx_hash": tx_hash, "raw": data}


async def verify(payment_id: str, tx_hash: str, network: str, asset: str, amount_usd: float) -> dict[str, Any]:
    """Ask the facilitator to verify the on-chain proof."""
    return await _post("verify", _payload(payment_id, tx_hash, network, asset, amount_usd))


async def settle(payment_id: str, tx_hash: str, network: str, asset: str, amount_usd: float) -> dict[str, Any]:
    """Ask the facilitator to settle (idempotent on its side)."""
    return await _post("settle", _payload(payment_id, tx_hash, network, asset, amount_usd))
