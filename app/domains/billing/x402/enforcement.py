"""x402 Payment Enforcement — thin orchestrator.

P6 Complexity Refactor (Win #18 from fleet audit, 2026-07-17):
Split the 2,818-line monolith (cognitive 310, cyclomatic 77) into
strategy classes with single responsibilities.

Architecture:
  route_classifier.py   — path matching, free-path detection, tool ID extraction
  tier_resolver.py      — developer keys, trial checking, burst rate limiting
  payment_verifier.py   — payment header parsing, facilitator routing, EVM self-verify
  enforcement_action.py — 402/403/429/400 response builders, bazaar extensions
  pricing_catalog.py    — CHAIN_USDC, TOOL_PRICES, chain names, pay-to addresses
  admin_endpoints.py    — discovery, revenue, trial status, refund, pricing suggestions

This module is a thin re-export layer (~80 lines). All behavior-delegating
functions are imported from the strategy modules and re-exported with the
identical names and signatures. No business logic lives here.

The x402_enforcement_middleware is DEPRECATED — it adds deprecation headers
and passes through. Real enforcement moved to app.domains.x402.middleware.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.domains.billing.x402.admin_endpoints import (
    _build_discovery_response,
    _verify_refund_ownership,
    get_pricing_suggestions,
    get_revenue,
    get_trial_status,
    request_refund,
    x402_discovery,
)
from app.domains.billing.x402.enforcement_action import (
    SECURITY_HEADERS,
    _build_accepts_list,
    _build_bazaar_extension,
    _build_resource_info,
    _record_refundable_payment,
    build_402_response,
)
from app.domains.billing.x402.payment_verifier import (
    VERIFIER,
    _detect_token_from_asset,
    _ensure_verifier,
    check_idempotency,
    parse_x_pay_header,
    self_verify_evm_usdc,
    verify_payment,
    verify_payment_via_router,
)

# ── Strategy module imports ──────────────────────────────────────
from app.domains.billing.x402.pricing_catalog import (
    CHAIN_USDC,
    TOOL_PRICES,
    _build_extra,
    _ensure_pay_addresses,
    _ensure_tool_prices,
    _get_pay_to_address,
    _load_tool_prices,
    _resolve_asset,
    _resolve_pay_to,
)
from app.domains.billing.x402.route_classifier import (
    is_x402_tool_path,
)
from app.domains.billing.x402.tier_resolver import (
    check_trial,
    consume_trial,
)

if TYPE_CHECKING:
    from fastapi import Request, Response

logger = get_logger("x402_enforcement")


async def x402_enforcement_middleware(request: Request, call_next: Any) -> Response:
    """[DEPRECATED] x402 payment enforcement middleware.

    Adds deprecation headers to all x402-tools responses.
    Real enforcement has moved to app.domains.x402.middleware.require_payment().
    """
    if not is_x402_tool_path(request.url.path):
        return await call_next(request)  # type: ignore[no-any-return]

    response = await call_next(request)
    response.headers["X-Deprecated"] = "true"
    response.headers["X-Deprecated-Reason"] = "Use domain/x402/middleware.py instead"
    return response  # type: ignore[no-any-return]


# ── PEP 562: lazy globals that get rebound after module load ──────
def __getattr__(name: str) -> Any:
    """Resolve rebound globals from their source modules at access time."""
    if name in ("EVM_PAY_TO", "SOL_PAY_TO"):
        import app.domains.billing.x402.pricing_catalog as _pc

        return getattr(_pc, name)
    if name == "VERIFIER":
        import app.domains.billing.x402.payment_verifier as _pv

        return _pv.VERIFIER
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Legacy non-blocking Redis helper ─────────────────────────────
async def get_redis_async() -> Any:
    """Get async Redis client."""
    try:
        from app.core.redis import get_redis_async as _get_async

        return _get_async()
    except (AttributeError, ImportError):
        return None


# ── X402Enforcer class (OO facade) ───────────────────────────────
class X402Enforcer:
    """Class-based facade for x402 payment enforcement.

    All state is module-level (TOOL_PRICES, CHAIN_USDC, VERIFIER),
    matching the legacy behavior of x402_enforcement_middleware().
    """

    def __init__(self) -> None:
        self.tool_prices = TOOL_PRICES
        self.chain_usdc = CHAIN_USDC
        self.security_headers = SECURITY_HEADERS
        self._verifier_loaded = False

    async def enforce(self, request: Request, call_next: Any) -> Response:
        """Run the x402 payment enforcement middleware."""
        return await x402_enforcement_middleware(request, call_next)

    def check_trial(self, tool_id: str, client_id: str, max_trials: int = 3) -> tuple[bool, int]:
        return check_trial(tool_id, client_id, max_trials)

    def consume_trial(self, tool_id: str, client_id: str, max_trials: int = 3) -> bool:
        return consume_trial(tool_id, client_id, max_trials)

    async def verify_payment(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await verify_payment(payload)

    def build_402_response(self, tool_id: str, client_id: str = "") -> Any:
        return build_402_response(tool_id, client_id)

    def ensure_tool_prices(self) -> None:
        return _ensure_tool_prices()

    def ensure_pay_addresses(self) -> None:
        return _ensure_pay_addresses()


# ── Public API (backward-compatible with full enforcement.py surface) ──
__all__ = [
    "CHAIN_USDC",
    "EVM_PAY_TO",
    "SECURITY_HEADERS",
    "SOL_PAY_TO",
    "TOOL_PRICES",
    "VERIFIER",
    "X402Enforcer",
    "_build_accepts_list",
    "_build_bazaar_extension",
    "_build_discovery_response",
    "_build_extra",
    "_build_resource_info",
    "_detect_token_from_asset",
    "_ensure_pay_addresses",
    "_ensure_tool_prices",
    "_ensure_verifier",
    "_get_pay_to_address",
    "_load_tool_prices",
    "_record_refundable_payment",
    "_resolve_asset",
    "_resolve_pay_to",
    "_verify_refund_ownership",
    "build_402_response",
    "check_idempotency",
    "check_trial",
    "consume_trial",
    "get_pricing_suggestions",
    "get_redis_async",
    "get_revenue",
    "get_trial_status",
    "parse_x_pay_header",
    "request_refund",
    "self_verify_evm_usdc",
    "verify_payment",
    "verify_payment_via_router",
    "x402_discovery",
    "x402_enforcement_middleware",
]
