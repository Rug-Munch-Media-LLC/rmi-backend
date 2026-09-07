"""Security tests: premium-tier x402 payment requirement (audit #8).

Revenue-leak regression: /auth/premium/signup must NOT grant premium tiers
(scout/hunter/pro) without a coffer-facilitator-verified x402 payment. The
coffer HTTP surface is mocked with respx — the real facilitator is never
contacted, and premium must FAIL CLOSED on any transport/verification error.

  - coffer ok          -> premium granted
  - coffer down        -> premium DENIED (never granted on transport error)
  - bad payment        -> premium DENIED (never granted on verification failure)
  - missing payment id -> 402 payment required
  - free tier          -> unchanged, no payment required
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import Request

from app.auth.x402_client import VERIFY_PATH, verify_payment
from app.core.errors import PaymentRequiredError
from app.routers.auth import PAYMENT_ID_HEADER, PremiumSignupRequest, premium_signup

# Pinned in conftest.py (force-assigned before any app.config import) so the
# settings singleton always resolves the mocked URL regardless of import order.
from tests.security.conftest import TEST_X402_FACILITATOR_URL

VERIFY_URL = f"{TEST_X402_FACILITATOR_URL}{VERIFY_PATH}"


def _auth_request(headers: dict[str, str] | None = None) -> Request:
    """Minimal Starlette Request scope for direct handler invocation."""
    headers = headers or {}
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/auth/premium/signup",
        "raw_path": b"/api/v1/auth/premium/signup",
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    return Request(scope)


USER = {"id": "user-abc-123", "email": "alice@rugmunch.io"}


# ═══════════════════════════════════════════════════════════════════════
# Client-level: verify_payment
# ═══════════════════════════════════════════════════════════════════════


class TestVerifyPaymentClient:
    """app.auth.x402_client.verify_payment fail-closed semantics."""

    @respx.mock
    async def test_coffer_ok_returns_verified(self) -> None:
        """A 200 {'verified': true} from the facilitator grants the payment."""
        respx.post(VERIFY_URL).mock(
            return_value=httpx.Response(200, json={"verified": True, "reason": "ok", "payment_id": "pay_1"})
        )
        result = await verify_payment("pay_1", "user:user-abc-123", "premium:pro")
        assert result["verified"] is True
        assert result["payment_id"] == "pay_1"

    @respx.mock
    async def test_coffer_down_denies(self) -> None:
        """Facilitator unreachable -> denied, never granted (fail-closed)."""
        route = respx.post(VERIFY_URL).mock(
            side_effect=httpx.ConnectError("coffer down", request=httpx.Request("POST", VERIFY_URL))
        )
        result = await verify_payment("pay_1", "user:user-abc-123", "premium:pro")
        assert result["verified"] is False
        assert "unreachable" in result["reason"]
        # Bounded retry exhausted — do not hammer the facilitator forever.
        assert route.call_count <= 3

    @respx.mock
    async def test_bad_payment_denies(self) -> None:
        """A 200 {'verified': false} from the facilitator denies the payment."""
        respx.post(VERIFY_URL).mock(
            return_value=httpx.Response(200, json={"verified": False, "reason": "payment not found"})
        )
        result = await verify_payment("pay_bogus", "user:user-abc-123", "premium:pro")
        assert result["verified"] is False
        assert result["reason"] == "payment not found"

    @respx.mock
    async def test_http_error_denies(self) -> None:
        """A non-200 facilitator response denies the payment."""
        respx.post(VERIFY_URL).mock(return_value=httpx.Response(402, json={"error": "unpaid"}))
        result = await verify_payment("pay_1", "user:user-abc-123", "premium:pro")
        assert result["verified"] is False
        assert "402" in result["reason"]


# ═══════════════════════════════════════════════════════════════════════
# Router-level: premium_signup enforcement
# ═══════════════════════════════════════════════════════════════════════


class TestPremiumSignupX402Gate:
    """Premium activation requires a coffer-verified payment for the caller."""

    @respx.mock
    async def test_coffer_ok_grants_premium(self) -> None:
        """Coffer confirms payment -> premium tier activated."""
        respx.post(VERIFY_URL).mock(return_value=httpx.Response(200, json={"verified": True, "payment_id": "pay_ok"}))
        result = await premium_signup(
            request=_auth_request({PAYMENT_ID_HEADER: "pay_ok"}),
            body=PremiumSignupRequest(tier="pro"),
            user=USER,
        )
        assert result["status"] == "active"
        assert result["tier"] == "pro"
        assert result["payment_id"] == "pay_ok"

    @respx.mock
    async def test_coffer_down_denies_premium(self) -> None:
        """Facilitator unreachable -> premium DENIED, never granted."""
        respx.post(VERIFY_URL).mock(
            side_effect=httpx.ConnectError("coffer down", request=httpx.Request("POST", VERIFY_URL))
        )
        with pytest.raises(PaymentRequiredError) as exc_info:
            await premium_signup(
                request=_auth_request({PAYMENT_ID_HEADER: "pay_ok"}),
                body=PremiumSignupRequest(tier="pro"),
                user=USER,
            )
        assert exc_info.value.status_code == 402

    @respx.mock
    async def test_bad_payment_denies_premium(self) -> None:
        """Coffer rejects the payment -> premium DENIED."""
        respx.post(VERIFY_URL).mock(
            return_value=httpx.Response(200, json={"verified": False, "reason": "unknown payment"})
        )
        with pytest.raises(PaymentRequiredError) as exc_info:
            await premium_signup(
                request=_auth_request({PAYMENT_ID_HEADER: "pay_bogus"}),
                body=PremiumSignupRequest(tier="hunter"),
                user=USER,
            )
        assert exc_info.value.status_code == 402

    @respx.mock
    async def test_missing_payment_id_denies_premium(self) -> None:
        """No X-Payment-ID header -> 402 before any facilitator call."""
        route = respx.post(VERIFY_URL).mock(
            return_value=httpx.Response(200, json={"verified": True, "payment_id": "pay_ok"})
        )
        with pytest.raises(PaymentRequiredError) as exc_info:
            await premium_signup(
                request=_auth_request(),
                body=PremiumSignupRequest(tier="scout"),
                user=USER,
            )
        assert exc_info.value.status_code == 402
        assert route.called is False  # no payment to verify — nothing quoted

    @respx.mock
    async def test_free_tier_unchanged(self) -> None:
        """Free/basic tier flows through without any payment requirement."""
        route = respx.post(VERIFY_URL).mock(
            return_value=httpx.Response(200, json={"verified": True, "payment_id": "pay_ok"})
        )
        result = await premium_signup(
            request=_auth_request(),  # no X-Payment-ID
            body=PremiumSignupRequest(tier="free"),
            user=USER,
        )
        assert result["status"] == "active"
        assert result["tier"] == "free"
        assert route.called is False  # facilitator never contacted for free tier
