"""Cross-system E2E tests: frontend → backend → scanner pipeline.

Verifies that the critical path works end-to-end:
  1. Token scan:  API request → DataBus enrichment → risk score computation → response shape
  2. Alert subscription:  subscribe → list → fire scan → verify alert fires
  3. Payment flow:  buy credits → webhook → verify balance update

All external calls (blockchain RPCs, price APIs, Stripe) are mocked.
Uses FastAPI TestClient (in-process, no real HTTP server).

Created as part of AUDIT-2026-Q3 remediation — zero cross-system tests found.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Set RMI_INTERNAL_KEY before any app imports — needed for unified_scanner_router to mount.
os.environ.setdefault("RMI_INTERNAL_KEY", "test-internal-key-e2e-1234567890")

import pytest

# Ensure repo root on path so app.* imports work
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def client():
    """Build a FastAPI TestClient once per module."""
    from app.factory import create_app

    app = create_app()
    return TestClient(app)


# Double-submit cookie CSRF pair — the CSRF middleware (app/middleware_setup.py)
# requires a matching X-CSRF-Token header + csrf_token cookie for state-changing
# requests that carry no API-key/Bearer credential.
CSRF_HEADERS = {"X-CSRF-Token": "test-csrf-token"}
CSRF_COOKIES = {"csrf_token": "test-csrf-token"}


# ── Mock data factories ──────────────────────────────────────────────────────


def _mock_databus_safe_token():
    """DataBus response for a SAFE token (all checks pass)."""
    return {
        "token_price": {
            "price_usd": 1.25,
            "market_cap_usd": 50_000_000,
            "volume_24h": 2_000_000,
            "liquidity_usd": 5_000_000,
            "source": "dexscreener",
        },
        "token_metadata": {
            "name": "SafeToken",
            "symbol": "SAFE",
            "age_hours": 720,  # 30 days
        },
        "token_security": {
            "source": "goplus",
            "checks": {
                "is_honeypot": {"status": "pass"},
                "is_proxy": {"status": "pass"},
                "is_mintable": {"status": "pass"},
                "owner_can_change_balance": {"status": "pass"},
                "is_blacklisted": {"status": "pass"},
            },
        },
        "holder_data": {"top_10_pct": 25},
        "holder_health": {"gini": 0.4},
        "liquidity_risk": {"locked_pct": 95},
        "dev_reputation": {"risk_score": 5},
        "rug_patterns": {"match_count": 0},
        "volume_authenticity": {"fake_volume_pct": 3},
    }


def _mock_databus_risky_token():
    """DataBus response for a RISKY token (multiple red flags)."""
    return {
        "token_price": {
            "price_usd": 0.0001,
            "market_cap_usd": 50_000,
            "volume_24h": 500,
            "liquidity_usd": 10_000,
            "source": "dexscreener",
        },
        "token_metadata": {
            "name": "RugToken",
            "symbol": "RUG",
            "age_hours": 2,  # very new
        },
        "token_security": {
            "source": "goplus",
            "checks": {
                "is_honeypot": {"status": "fail"},
                "is_proxy": {"status": "fail"},
                "is_mintable": {"status": "fail"},
            },
        },
        "holder_data": {"top_10_pct": 95},
        "holder_health": {"gini": 0.92},
        "liquidity_risk": {"locked_pct": 10},
        "dev_reputation": {"risk_score": 85},
        "rug_patterns": {"match_count": 2},
        "volume_authenticity": {"fake_volume_pct": 75},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: TOKEN SCAN E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestTokenScanE2E:
    """End-to-end token scan: API → DataBus mock → scanner → risk score → response."""

    @pytest.mark.e2e
    def test_scan_safe_token_produces_high_safety_score(self, client):
        """A token with all-green checks should score >= 80 (safe/low risk)."""
        mock_data = _mock_databus_safe_token()

        # databus is lazily imported inside _fetch_batch via `from app.databus import databus`,
        # so we patch at the source module level where the name lives.
        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(side_effect=lambda chain_name, **kwargs: mock_data.get(chain_name))
        with patch("app.databus.databus", mock_db):
            resp = client.post(
                "/api/v2/scanner/token/scan",
                json={"address": "0xSafeToken1234567890abcdef", "chain": "ethereum"},
                headers={"X-RMI-Key": os.environ["RMI_INTERNAL_KEY"]},
            )

        assert resp.status_code == 200
        body = resp.json()

        # ── Response shape contract ──
        assert body["status"] == "ok"
        assert body["token"] == "0xSafeToken1234567890abcdef"
        assert body["chain"] == "ethereum"
        assert body["name"] == "SafeToken"
        assert body["symbol"] == "SAFE"
        assert isinstance(body["price_usd"], (int, float))
        assert isinstance(body["safety_score"], (int, float))
        assert isinstance(body["confidence"], (int, float))
        assert isinstance(body["risk_level"], str)
        assert isinstance(body["risk_flags"], list)
        assert isinstance(body["green_flags"], list)
        assert isinstance(body["modules_run"], list)
        assert isinstance(body["scan_duration_ms"], (int, float))

        # ── Risk score logic ──
        assert body["safety_score"] >= 80, f"Safe token should score >= 80, got {body['safety_score']}"
        assert body["risk_level"] in ("safe", "low"), f"Safe token should be safe/low, got {body['risk_level']}"
        # Should have green flags from passing security checks
        assert len(body["green_flags"]) > 0
        # Should NOT have critical risk flags
        assert "is_honeypot" not in body["risk_flags"]
        assert "LP_LOCK_LOW" not in body["risk_flags"]
        assert "DEV_HIGH_RISK" not in body["risk_flags"]

    @pytest.mark.e2e
    def test_scan_risky_token_produces_low_safety_score(self, client):
        """A token with honeypot, low liquidity, and rug patterns should score < 40."""
        mock_data = _mock_databus_risky_token()

        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(side_effect=lambda chain_name, **kwargs: mock_data.get(chain_name))
        with patch("app.databus.databus", mock_db):
            resp = client.post(
                "/api/v2/scanner/token/scan",
                json={"address": "0xRugToken1234567890abcdef", "chain": "ethereum"},
                headers={"X-RMI-Key": os.environ["RMI_INTERNAL_KEY"]},
            )

        assert resp.status_code == 200
        body = resp.json()

        # ── Response shape ──
        assert body["status"] == "ok"
        assert body["name"] == "RugToken"
        assert body["symbol"] == "RUG"

        # ── Risk score logic ──
        assert body["safety_score"] < 40, f"Risky token should score < 40, got {body['safety_score']}"
        assert body["risk_level"] in ("high", "critical"), (
            f"Risky token should be high/critical, got {body['risk_level']}"
        )
        # Should have flagged risk indicators
        assert len(body["risk_flags"]) > 0

    @pytest.mark.e2e
    def test_scan_result_has_all_expected_fields(self, client):
        """Every field the frontend expects is present and typed correctly."""
        mock_data = _mock_databus_safe_token()

        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(side_effect=lambda chain_name, **kwargs: mock_data.get(chain_name))
        with patch("app.databus.databus", mock_db):
            resp = client.post(
                "/api/v2/scanner/token/scan",
                json={"address": "0xTestFieldShape1234567890", "chain": "solana"},
            )

        assert resp.status_code == 200
        body = resp.json()

        expected_fields = [
            "status",
            "token",
            "chain",
            "name",
            "symbol",
            "price_usd",
            "safety_score",
            "confidence",
            "risk_level",
            "risk_flags",
            "green_flags",
            "modules_run",
            "scan_duration_ms",
            "tier",
            "scanned_at",
        ]
        for field in expected_fields:
            assert field in body, f"Missing field: {field}"

        # Tier should be passed through
        assert body["tier"] == "free"

        # scanned_at should be a valid ISO timestamp
        assert "T" in body["scanned_at"]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: ALERT SUBSCRIPTION E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlertSubscriptionE2E:
    """End-to-end alert flow: subscribe → list → trigger scan → verify alert fires."""

    @pytest.mark.e2e
    def test_subscribe_and_list(self, client):
        """Subscribe to a whale alert, then list subscriptions — should appear."""
        resp = client.post(
            "/api/v1/whale-alerts/subscribe",
            json={
                "user_id": "test-user-e2e",
                "address": "0xWhaleAddress1234567890abcdef",
                "chain": "ethereum",
                "threshold_usd": 500000,
                "notify_telegram": False,
                "label": "Test Whale",
            },
            headers=CSRF_HEADERS,
            cookies=CSRF_COOKIES,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "subscribed"
        assert "id" in body

        # List subscriptions
        resp_list = client.get("/api/v1/whale-alerts/subscriptions/test-user-e2e")
        assert resp_list.status_code == 200
        subs = resp_list.json()["subscriptions"]
        assert len(subs) >= 1
        assert any(s["address"] == "0xWhaleAddress1234567890abcdef" for s in subs)

    @pytest.mark.e2e
    def test_unsubscribe(self, client):
        """Subscribe then unsubscribe — subscription should be removed."""
        # Subscribe
        resp = client.post(
            "/api/v1/whale-alerts/subscribe",
            json={
                "user_id": "test-user-unsub",
                "address": "0xUnsubAddr1234567890abcdef",
                "chain": "ethereum",
                "threshold_usd": 100000,
            },
        )
        assert resp.status_code == 200
        alert_id = resp.json()["id"]

        # Unsubscribe
        resp_del = client.delete(f"/api/v1/whale-alerts/subscribe/{alert_id}")
        assert resp_del.status_code == 200
        assert resp_del.json()["status"] == "unsubscribed"

        # Verify removed
        resp_list = client.get("/api/v1/whale-alerts/subscriptions/test-user-unsub")
        subs = resp_list.json()["subscriptions"]
        assert all(s["id"] != alert_id for s in subs)

    @pytest.mark.e2e
    def test_scan_triggers_alerts_for_large_transfers(self, client):
        """Subscribe, then trigger scan — large transfers should fire alerts."""
        # Subscribe
        resp_sub = client.post(
            "/api/v1/whale-alerts/subscribe",
            json={
                "user_id": "test-user-fire",
                "address": "0xBigWhale1234567890abcdef",
                "chain": "ethereum",
                "threshold_usd": 100_000,
                "notify_telegram": False,
            },
        )
        assert resp_sub.status_code == 200

        mock_txs = {
            "data": {
                "transactions": [
                    {"value_usd": "250000", "hash": "0xabc123def456"},
                    {"value_usd": "50000", "hash": "0xdef789abc012"},
                ]
            }
        }

        # Simple response object — NOT an AsyncMock, because `await client.get()`
        # would unwrap it and return a new empty mock instead of this object.
        class _FakeResponse:
            status_code = 200

            def json(self):
                return mock_txs

        # Build a class that implements async context manager + get()
        # httpx.AsyncClient is called with timeout= kwarg, so __init__ must accept it.
        class _FakeAsyncClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                return _FakeResponse()

        with patch("app.routers.whale_alerts.httpx") as mock_httpx:
            mock_httpx.AsyncClient = _FakeAsyncClient
            resp = client.post("/api/v1/whale-alerts/scan")

        assert resp.status_code == 200
        body = resp.json()
        assert body["alerts_fired"] == 1  # Only the $250K transfer exceeds $100K threshold
        assert body["subscriptions_checked"] >= 1

    @pytest.mark.e2e
    def test_unsubscribe_nonexistent_returns_404(self, client):
        """Unsubscribing from a nonexistent alert returns 404."""
        resp = client.delete("/api/v1/whale-alerts/subscribe/nonexistent-id")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: PAYMENT FLOW E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestPaymentFlowE2E:
    """End-to-end payment: buy credits (sandbox) → webhook → verify balance."""

    @pytest.mark.e2e
    def test_buy_credits_sandbox_mode(self, client):
        """When Stripe is not configured, buying credits works in sandbox mode."""
        api_key = "test-api-key-e2e-12345"
        resp = client.post(
            f"/api/v1/credits/buy?api_key={api_key}&amount_usd=5000",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "sandbox"
        assert body["credits_added"] == 5000
        assert body["total_balance"] >= 5000

    @pytest.mark.e2e
    def test_check_balance_after_purchase(self, client):
        """After buying credits, balance endpoint reflects the purchase."""
        api_key = "test-api-key-balance-check"

        # Buy credits
        client.post(f"/api/v1/credits/buy?api_key={api_key}&amount_usd=2000")

        # Check balance
        resp = client.get(f"/api/v1/credits/balance?api_key={api_key}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["balance"] >= 2000
        assert body["total_purchased"] >= 2000
        assert body["free_tier"] is False

    @pytest.mark.e2e
    def test_webhook_simulates_payment_confirmation(self, client):
        """Stripe webhook processes checkout.session.completed and credits the account."""
        webhook_payload = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_123",
                    "metadata": {
                        "api_key": "test-api-key-webhook",
                        "package": "starter",
                    },
                }
            },
        }

        # STRIPE_ENABLED is False in test env, so webhook returns early without processing.
        # Mock it to True so the webhook actually processes the event.
        with patch("app.routers.stripe_integration.STRIPE_ENABLED", True):
            resp = client.post(
                "/api/v1/stripe/webhook",
                json=webhook_payload,
                headers={"stripe-signature": "test-sig"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["received"] is True
        assert body["processed"] is True

        # Verify credits were added
        resp_balance = client.get("/api/v1/credits/balance?api_key=test-api-key-webhook")
        balance_body = resp_balance.json()
        assert balance_body["balance"] >= 1000  # starter pack = 1000 credits

    @pytest.mark.e2e
    def test_list_packages_returns_all_tiers(self, client):
        """The packages endpoint returns all available credit packages."""
        resp = client.get("/api/v1/stripe/packages")
        assert resp.status_code == 200
        body = resp.json()

        # Should have both one-time and subscription packages
        assert "packages" in body
        assert "subscriptions" in body

        # Verify known packages exist
        assert "starter" in body["packages"]
        assert "growth" in body["packages"]
        assert "pro" in body["packages"]
        assert "enterprise" in body["packages"]

        # Verify structure
        starter = body["packages"]["starter"]
        assert "price_usd" in starter
        assert "credits" in starter
        assert "label" in starter
        assert starter["credits"] == 1000

    @pytest.mark.e2e
    def test_checkout_when_stripe_not_configured_returns_503(self, client):
        """Checkout endpoint returns 503 with package info when Stripe is off."""
        resp = client.post(
            "/api/v1/stripe/checkout",
            json={"package": "starter", "api_key": "test-key"},
        )
        # When STRIPE_ENABLED is False, returns 503 with helpful info
        assert resp.status_code == 503
        body = resp.json()
        assert "packages" in body or "error" in body

    @pytest.mark.e2e
    def test_buy_credits_requires_api_key(self, client):
        """Buying credits without an api_key returns 400."""
        resp = client.post("/api/v1/credits/buy?amount_usd=1000")
        assert resp.status_code == 400
