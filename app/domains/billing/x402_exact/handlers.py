"""x402 exact — the three paid resources (intel + security only).

Data comes from the live DataBus (same providers the rest of the platform
uses). Responses are normalised to a stable envelope so a bot can parse them
without knowing which upstream answered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.databus import databus
from app.domains.billing.x402_exact import idempotency
from app.domains.billing.x402_exact.catalog import resolve
from app.domains.billing.x402_exact.gate import require_paid

if TYPE_CHECKING:
    from fastapi import Request

    from app.domains.billing.x402_exact.models import ScanRequest, WalletRequest

log = get_logger(__name__)

_DROP = ("source", "is_local", "tier", "fallback_used", "consumer", "packaging", "cached")


def _clean(value: Any) -> dict[str, Any]:
    """Strip provider-identifying fields from a DataBus payload."""
    if not isinstance(value, dict):
        return {"result": value}
    cleaned = {k: v for k, v in value.items() if k not in _DROP}
    nested = cleaned.get("data")
    if isinstance(nested, dict):
        cleaned["data"] = {k: v for k, v in nested.items() if k not in _DROP}
    return cleaned


async def _serve(
    handle: str,
    request: Request,
    providers: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Gate, fetch every provider, and return the stable envelope."""
    from uuid import uuid4

    payment = await require_paid(handle, request)
    if payment.get("replayed") is not None:
        return payment["replayed"]

    endpoint = resolve(handle)
    assert endpoint is not None  # gate resolves the handle first
    collected: dict[str, Any] = {}
    for provider, kwargs in providers:
        try:
            collected[provider] = _clean(
                await databus.fetch(provider, consumer_type="x402_paid", x402_tier=endpoint.tier, **kwargs)
            )
        except (ValueError, TypeError, KeyError, RuntimeError, ConnectionError, TimeoutError) as exc:
            log.warning(f"x402_exact_provider_failed provider={provider}: {exc}")
            collected[provider] = {"error": "provider_unavailable"}

    body = {
        "ok": all("error" not in part for part in collected.values()),
        "resource": endpoint.path,
        "data": collected,
        "payment": {k: v for k, v in payment.items() if k != "replayed"},
        "request_id": getattr(request.state, "request_id", uuid4().hex),
    }
    idem_key = request.headers.get("Idempotency-Key", "")
    if idem_key:
        await idempotency.store(idem_key, handle, payment["payer"], body)
    return body


async def intel_scan(payload: ScanRequest, request: Request) -> dict[str, Any]:
    """$0.005 — token risk scan."""
    return await _serve(
        "intel.scan",
        request,
        [("risk_scan", {"address": payload.address, "chain": payload.chain})],
    )


async def security_wallet(payload: WalletRequest, request: Request) -> dict[str, Any]:
    """$0.02 — wallet security intel (threat screen + labels)."""
    return await _serve(
        "security.wallet",
        request,
        [
            ("threat_check", {"address": payload.address, "chain": payload.chain}),
            ("wallet_labels", {"address": payload.address, "chain": payload.chain}),
        ],
    )


async def scanner_token(payload: ScanRequest, request: Request) -> dict[str, Any]:
    """$0.01 — token security snapshot."""
    return await _serve(
        "scanner.token",
        request,
        [("token_security", {"address": payload.address, "chain": payload.chain})],
    )
