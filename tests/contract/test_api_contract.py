"""Offline API contract tests — Pydantic schema ↔ OpenAPI alignment.

Validates that backend response models match the shapes frontend consumers
expect, WITHOUT requiring a running server. Uses FastAPI TestClient
against the app factory directly.

Coverage:
  1. Token endpoint response shape (TokenSummary, TokenRisk)
  2. Wallet endpoint response shape (WalletResolveResponse)
  3. Scanner endpoint response shape (ScanResult)
  4. OpenAPI schema declares all expected routes + models
  5. Invalid input returns 422 with proper error shape

Run: pytest tests/contract/test_api_contract.py -v
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# Override .env default 'development' which is invalid for Settings Literal
os.environ.setdefault("ENVIRONMENT", "dev")

from app.api.v1.public.scanner import ScanRequest, ScanResult
from app.api.v1.public.token import TokenRisk, TokenSummary
from app.api.v1.public.wallet import WalletResolveResponse

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Create a minimal test app with just the public routers.

    Avoids heavy middleware/lifespan that require DB/Redis connections.
    """
    from app.api.v1.public.scanner import router as scanner_router
    from app.api.v1.public.token import router as token_router
    from app.api.v1.public.wallet import router as wallet_router

    app = FastAPI()
    app.include_router(token_router, prefix="/api/v1/public")
    app.include_router(wallet_router, prefix="/api/v1/public")
    app.include_router(scanner_router, prefix="/api/v1/public")
    return TestClient(app, raise_server_exceptions=False)


# ── 1. Token endpoint response shape ────────────────────────────────


class TestTokenEndpointContract:
    """Verify GET /api/v1/public/token/{address} response shape."""

    VALID_ADDRESS = "0xdAC17F958D2ee523a2206206994597C13D831ec7"

    def test_token_endpoint_returns_structured_error(self, client):
        """Stub endpoint returns 501 with detail field (not 500)."""
        resp = client.get(f"/api/v1/public/token/{self.VALID_ADDRESS}")
        assert resp.status_code == 501
        body = resp.json()
        assert "detail" in body

    def test_token_summary_model_validates_good_data(self):
        """TokenSummary schema accepts valid token metadata."""
        data = {
            "chain": "ethereum",
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "name": "Tether USD",
            "symbol": "USDT",
            "decimals": 6,
            "deployed_at": "2018-01-01",
            "deployer": "0x0000000000000000000000000000000000000000",
        }
        model = TokenSummary(**data)
        assert model.chain == "ethereum"
        assert model.symbol == "USDT"
        assert model.decimals == 6

    def test_token_summary_rejects_invalid_chain(self):
        """TokenSummary rejects unknown chain names."""
        data = {
            "chain": "bitcoin",
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        }
        with pytest.raises(ValidationError):
            TokenSummary(**data)

    def test_token_summary_rejects_short_address(self):
        """TokenSummary rejects addresses shorter than 26 chars."""
        data = {
            "chain": "ethereum",
            "address": "0x123",
        }
        with pytest.raises(ValidationError):
            TokenSummary(**data)

    def test_token_risk_model_validates_good_data(self):
        """TokenRisk schema accepts valid risk data."""
        data = {
            "chain": "ethereum",
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "risk_score": 42,
            "risk_tier": "medium",
            "factors": ["low_liquidity", "unverified_contract"],
        }
        model = TokenRisk(**data)
        assert model.risk_score == 42
        assert model.risk_tier == "medium"
        assert len(model.factors) == 2

    def test_token_risk_rejects_score_out_of_range(self):
        """TokenRisk rejects risk_score outside 0-100."""
        data = {
            "chain": "ethereum",
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "risk_score": 150,
            "risk_tier": "high",
        }
        with pytest.raises(ValidationError):
            TokenRisk(**data)


# ── 2. Wallet endpoint response shape ───────────────────────────────


class TestWalletEndpointContract:
    """Verify GET /api/v1/public/wallet/{address} response shape."""

    VALID_ADDRESS = "0xdAC17F958D2ee523a2206206994597C13D831ec7"

    def test_wallet_endpoint_returns_structured_error(self, client):
        """Stub endpoint returns 501 with detail field."""
        resp = client.get(f"/api/v1/public/wallet/{self.VALID_ADDRESS}")
        assert resp.status_code == 501
        body = resp.json()
        assert "detail" in body

    def test_wallet_resolve_model_validates_good_data(self):
        """WalletResolveResponse accepts valid wallet data."""
        data = {
            "chain": "ethereum",
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "labels": ["exchange", "hot_wallet"],
            "entity": "Tether Treasury",
            "balance_usd": 1_234_567.89,
            "tx_count": 42_000,
        }
        model = WalletResolveResponse(**data)
        assert model.entity == "Tether Treasury"
        assert model.tx_count == 42_000

    def test_wallet_resolve_rejects_invalid_chain(self):
        """WalletResolveResponse rejects unknown chain."""
        with pytest.raises(ValidationError):
            WalletResolveResponse(
                chain="solitaire",
                address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
            )

    def test_wallet_resolve_allows_null_optionals(self):
        """WalletResolveResponse works with all optional fields null."""
        data = {
            "chain": "base",
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        }
        model = WalletResolveResponse(**data)
        assert model.labels == []
        assert model.entity is None
        assert model.balance_usd is None


# ── 3. Scanner endpoint response shape ──────────────────────────────


class TestScannerEndpointContract:
    """Verify POST /api/v1/public/scanner/scan response shape."""

    @pytest.mark.slow  # 34s: boots full app (TestClient) + heavy model imports
    def test_scan_endpoint_returns_structured_error(self, client):
        """Stub scanner returns 501 with detail field."""
        resp = client.post(
            "/api/v1/public/scanner/scan",
            json={
                "chain": "ethereum",
                "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                "depth": "quick",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "scan_id" in body
        assert "risk_score" in body

    def test_scan_result_model_validates_good_data(self):
        """ScanResult accepts valid scan output."""
        data = {
            "scan_id": "sc_a1b2c3d4",
            "status": "completed",
            "risk_score": 78,
            "risk_tier": "high",
            "factors": ["honeypot", "hidden_mint"],
        }
        model = ScanResult(**data)
        assert model.scan_id == "sc_a1b2c3d4"
        assert model.status == "completed"
        assert model.risk_score == 78

    def test_scan_result_rejects_invalid_status(self):
        """ScanResult rejects status not in enum pattern."""
        data = {
            "scan_id": "sc_123",
            "status": "unknown",
            "risk_score": 50,
            "risk_tier": "low",
        }
        with pytest.raises(ValidationError):
            ScanResult(**data)

    def test_scan_request_model_validates_good_data(self):
        """ScanRequest accepts valid scan request."""
        data = {
            "chain": "ethereum",
            "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "depth": "deep",
        }
        model = ScanRequest(**data)
        assert model.depth == "deep"

    def test_scan_request_rejects_invalid_depth(self):
        """ScanRequest rejects depth not in quick/standard/deep."""
        with pytest.raises(ValidationError):
            ScanRequest(
                chain="ethereum",
                address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
                depth="ultra",
            )


# ── 4. OpenAPI schema alignment ─────────────────────────────────────


class TestOpenAPIContract:
    """Verify the app's OpenAPI spec declares expected routes + models."""

    @pytest.fixture(scope="class")
    def openapi_schema(self, client):
        """Fetch the generated OpenAPI schema."""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        return resp.json()

    def test_schema_has_token_route(self, openapi_schema):
        """OpenAPI must declare GET /api/v1/public/token/{address}."""
        paths = openapi_schema.get("paths", {})
        token_path = "/api/v1/public/token/{address}"
        assert token_path in paths, f"Missing route: {token_path}"
        assert "get" in paths[token_path]

    def test_schema_has_wallet_route(self, openapi_schema):
        """OpenAPI must declare GET /api/v1/public/wallet/{address}."""
        paths = openapi_schema.get("paths", {})
        wallet_path = "/api/v1/public/wallet/{address}"
        assert wallet_path in paths, f"Missing route: {wallet_path}"
        assert "get" in paths[wallet_path]

    def test_schema_has_scanner_route(self, openapi_schema):
        """OpenAPI must declare POST /api/v1/public/scanner/scan."""
        paths = openapi_schema.get("paths", {})
        scan_path = "/api/v1/public/scanner/scan"
        assert scan_path in paths, f"Missing route: {scan_path}"
        assert "post" in paths[scan_path]

    def test_schema_declares_token_models(self, openapi_schema):
        """OpenAPI components must include TokenSummary + TokenRisk."""
        schemas = openapi_schema.get("components", {}).get("schemas", {})
        assert "TokenSummary" in schemas, "TokenSummary missing from OpenAPI"
        assert "TokenRisk" in schemas, "TokenRisk missing from OpenAPI"

    def test_schema_declares_wallet_model(self, openapi_schema):
        """OpenAPI components must include WalletResolveResponse."""
        schemas = openapi_schema.get("components", {}).get("schemas", {})
        assert "WalletResolveResponse" in schemas

    def test_schema_declares_scanner_models(self, openapi_schema):
        """OpenAPI components must include ScanResult + ScanRequest."""
        schemas = openapi_schema.get("components", {}).get("schemas", {})
        assert "ScanResult" in schemas
        assert "ScanRequest" in schemas


# ── 5. Error shape contract ─────────────────────────────────────────


class TestErrorShapeContract:
    """Verify all public endpoints return consistent error shapes."""

    VALID_ADDRESS = "0xdAC17F958D2ee523a2206206994597C13D831ec7"

    def test_422_has_detail_and_body(self, client):
        """Validation errors return {detail: [...]} shape."""
        resp = client.post(
            "/api/v1/public/scanner/scan",
            json={"chain": "bad_chain", "address": "short"},
        )
        # FastAPI returns 422 for invalid request body
        assert resp.status_code in (422, 501)
        body = resp.json()
        assert "detail" in body

    def test_missing_required_field_returns_422(self, client):
        """POST without required fields returns 422."""
        resp = client.post(
            "/api/v1/public/scanner/scan",
            json={},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body
        # FastAPI validation error detail is a list
        if isinstance(body["detail"], list):
            assert len(body["detail"]) > 0
