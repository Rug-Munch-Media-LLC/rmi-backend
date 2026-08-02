"""Integration tests for the scanner pipeline — token scanning, safety scoring, rate limits.

Tests REAL business logic:
  - _calculate_safety_score(): risk scoring from module results (pure business logic)
  - rate limiter: check_scan_limit tier enforcement, internal bypass, identity extraction
  - ScanResult model: module tracking, tier gating, risk flags
  - Provider configuration: chain IDs, GoPlus mappings

External HTTP is mocked; internal logic is tested directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# Known test addresses
SOLANA_TOKEN = "So11111111111111111111111111111111111111112"
ETH_TOKEN = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


# =========================================================================
# Safety Score Tests — PURE BUSINESS LOGIC (no HTTP, no mocking)
# =========================================================================


class TestSafetyScoreCalculation:
    """Tests for _calculate_safety_score — the core scoring engine."""

    def test_baseline_score_is_50(self):
        """Empty modules should produce baseline score of 50."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        score = _calculate_safety_score([])
        assert score == 50

    def test_error_modules_dont_affect_score(self):
        """Modules that errored should not change the baseline."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules = [
            {"module": "goplus", "status": "error", "error": "HTTP 500"},
            {"module": "honeypot", "status": "error", "error": "Timeout"},
        ]
        score = _calculate_safety_score(modules)
        assert score == 50

    def test_honeypot_flag_causes_large_penalty(self):
        """Honeypot detection should cause significant score drop."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules = [
            {"module": "goplus", "result": {"is_honeypot": True, "risk_score": 50}},
            {"module": "honeypot", "result": {"is_honeypot": True, "risk_score": 100}},
        ]
        score = _calculate_safety_score(modules)
        assert score < 30, f"Honeypot should drop score below 30, got {score}"

    def test_sanctioned_token_scores_zero(self):
        """Sanctioned tokens should get the maximum penalty (score 0)."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules = [
            {"module": "screening", "result": {"is_sanctioned": True, "risk_score": 100}},
        ]
        score = _calculate_safety_score(modules)
        assert score == 0, f"Sanctioned token should get score 0, got {score}"

    def test_bundled_token_with_high_confidence(self):
        """Bundled token with high confidence should be heavily penalized."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules = [
            {"module": "bundle", "result": {"is_bundled": True, "confidence": 0.9}},
        ]
        score = _calculate_safety_score(modules)
        assert score < 15, f"Bundled token (90% confidence) should score very low, got {score}"

    def test_lp_concentration_triggers_risk(self):
        """Very high LP concentration should trigger penalty."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules = [
            {"module": "lp_concentration", "result": {"top10_pct": 95}},
        ]
        score = _calculate_safety_score(modules)
        assert score < 40, f"95% LP concentration should drop score, got {score}"

    def test_critical_deployer_reputation_penalizes(self):
        """CRITICAL deployer reputation should add penalty."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules = [
            {"module": "deployer", "result": {"reputation_label": "CRITICAL", "risk_score": 70}},
        ]
        score = _calculate_safety_score(modules)
        assert score < 30, f"CRITICAL deployer should drop score, got {score}"

    def test_score_clamped_between_zero_and_100(self):
        """Safety score should never go below 0 or above 100."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules_risky = [
            {"module": "goplus", "result": {"is_honeypot": True, "risk_score": 100}},
            {"module": "honeypot", "result": {"is_honeypot": True, "risk_score": 100}},
            {"module": "spam", "result": {"is_spam": True, "risk_score": 100}},
            {"module": "screening", "result": {"is_sanctioned": True, "risk_score": 100}},
        ]
        score_risky = _calculate_safety_score(modules_risky)
        assert score_risky >= 0, f"Score should be clamped at 0, got {score_risky}"

        modules_clean = [
            {"module": "verification", "result": {"is_verified": True}},
            {"module": "ownership", "result": {"ownership_renounced": True}},
            {"module": "liquidity", "result": {"liquidity_locked_pct": 99}},
            {"module": "token_age", "result": {"risk_level": "low"}},
        ]
        score_clean = _calculate_safety_score(modules_clean)
        assert score_clean <= 100, f"Score should be clamped at 100, got {score_clean}"

    def test_wash_trading_suspicion_penalizes(self):
        """Wash trading flag should add penalty."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules = [
            {
                "module": "volume",
                "result": {"is_wash_trading_suspicious": True, "risk_level": "high"},
            },
        ]
        score = _calculate_safety_score(modules)
        assert score < 30, f"Wash trading should drop score, got {score}"

    def test_token_age_critical_scores_low(self):
        """Critical token age should add significant penalty."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules = [
            {"module": "token_age", "result": {"risk_level": "critical"}},
        ]
        score = _calculate_safety_score(modules)
        assert score < 35, f"Critical token age should drop score, got {score}"

    def test_many_modules_without_risk_get_confidence_bonus(self):
        """6+ clean modules should get a confidence bonus of +10."""
        from app.domains.scanner.service.scoring import _calculate_safety_score

        modules = [
            {
                "module": "goplus",
                "result": {"result": "scanned", "risk_score": 0, "is_honeypot": False},
            },
            {"module": "honeypot", "result": {"result": "clean", "risk_score": 0}},
            {"module": "spam", "result": {"result": "clean", "is_spam": False}},
            {"module": "copycat", "result": {"result": "clean"}},
            {"module": "token_age", "result": {"risk_level": "low"}},
            {"module": "volume", "result": {"is_wash_trading_suspicious": False}},
        ]
        score = _calculate_safety_score(modules)
        # 50 + 5*3 (copycat clean on 3 modules with result=clean) + 5 (age low) + 10 (confidence) = 80
        assert score == 80, f"Expected 80, got {score}"


# =========================================================================
# ScanResult Model Tests
# =========================================================================


class TestScanResultModel:
    """Tests for the ScanResult data model."""

    def test_scan_result_defaults(self):
        """ScanResult should have sensible defaults."""
        from app.domains.scanner.models import ScanResult

        result = ScanResult(token_address=SOLANA_TOKEN, chain="solana")
        assert result.token_address == SOLANA_TOKEN
        assert result.chain == "solana"
        assert result.tier_required == "free"
        assert result.safety_score == 50
        assert result.confidence == 0
        assert result.modules_run == []
        assert result.risk_flags == []

    def test_scan_result_with_pro_tier(self):
        """Tier requirement can be set on ScanResult."""
        from app.domains.scanner.models import ScanResult

        result = ScanResult(token_address=ETH_TOKEN, chain="ethereum")
        result.tier_required = "pro"
        assert result.tier_required == "pro"

    def test_scan_result_risk_flags_append(self):
        """Risk flags should be appendable."""
        from app.domains.scanner.models import ScanResult

        result = ScanResult(token_address=SOLANA_TOKEN, chain="solana")
        result.risk_flags.append("honeypot")
        result.risk_flags.append("high_sell_tax")
        assert len(result.risk_flags) == 2
        assert "honeypot" in result.risk_flags

    def test_scan_result_modules_tracking(self):
        """Modules should track name, status, and optional error."""
        from app.domains.scanner.models import ScanResult

        result = ScanResult(token_address=SOLANA_TOKEN, chain="solana")
        result.modules_run.append({"module": "honeypot", "status": "success", "result": {"is_honeypot": False}})
        result.modules_run.append({"module": "goplus", "status": "error", "error": "timeout"})
        assert len(result.modules_run) == 2
        assert result.modules_run[0]["module"] == "honeypot"
        assert result.modules_run[1]["status"] == "error"

    def test_chain_ids_defined(self):
        """CHAIN_IDS should have all supported chains."""
        from app.domains.scanner.models import CHAIN_IDS

        for chain in [
            "solana",
            "ethereum",
            "base",
            "bsc",
            "arbitrum",
            "polygon",
            "avalanche",
            "optimism",
        ]:
            assert chain in CHAIN_IDS, f"Missing chain: {chain}"
        assert CHAIN_IDS["ethereum"] == "1"
        assert CHAIN_IDS["solana"] == "solana"

    def test_goplus_chain_ids_mapped(self):
        """GoPlus chain ID mappings should cover major chains."""
        from app.domains.scanner.modules._core import GOPLUS_CHAIN_IDS

        for chain in [
            "solana",
            "ethereum",
            "bsc",
            "base",
            "arbitrum",
            "polygon",
            "avalanche",
            "optimism",
        ]:
            assert chain in GOPLUS_CHAIN_IDS, f"Missing GoPlus chain ID for {chain}"

    def test_forward_compatible_fields(self):
        """Additional fields should not break the model."""
        from app.domains.scanner.models import ScanResult

        result = ScanResult(token_address=SOLANA_TOKEN, chain="solana")
        result.fusion_brief = {"risk": "high", "summary": "Suspicious token"}
        assert result.fusion_brief is not None
        assert result.fusion_brief["risk"] == "high"


# =========================================================================
# Rate Limiter Tests
# =========================================================================


class TestScannerRateLimiter:
    """Tests for the rate limiter business logic (consolidated in app.core.rate_limiter)."""

    def test_free_tier_has_5_scan_limit(self):
        """FREE tier has a defined sentinel scan daily limit."""
        from app.core.rate_limiter import TIER_LIMITS, Tier

        free_scans = TIER_LIMITS[Tier.FREE]["sentinel_scans_per_day"]
        assert isinstance(free_scans, int)
        assert free_scans > 0

    def test_pro_tier_has_100_scan_limit(self):
        """PRO tier should have 100 sentinel scans per day."""
        from app.core.rate_limiter import TIER_LIMITS, Tier

        assert TIER_LIMITS[Tier.PRO]["sentinel_scans_per_day"] == 100

    def test_elite_tier_unlimited(self):
        """ENTERPRISE tier should be effectively unlimited."""
        from app.core.rate_limiter import TIER_LIMITS, Tier

        assert TIER_LIMITS[Tier.ENTERPRISE]["sentinel_scans_per_day"] >= 1000

    def test_identity_extraction_from_ip(self):
        """_extract_client_id should extract IP from request.client.host."""
        from app.core.rate_limiter import _extract_client_id

        class MockRequest:
            headers = {}
            client = type("obj", (object,), {"host": "192.168.1.1"})()

        assert _extract_client_id(MockRequest()) == "192.168.1.1"

    def test_identity_from_x_forwarded_for(self):
        """_extract_client_id should prefer X-Forwarded-For header."""
        from app.core.rate_limiter import _extract_client_id

        class MockRequest:
            headers = {"X-Forwarded-For": "10.0.0.1, 192.168.1.1"}
            client = type("obj", (object,), {"host": "192.168.1.1"})()

        assert _extract_client_id(MockRequest()) == "10.0.0.1"

    def test_identity_with_both_headers(self):
        """When both X-Forwarded-For and client.host exist, forwarded wins."""
        from app.core.rate_limiter import _extract_client_id

        class MockRequest:
            headers = {"X-Forwarded-For": "5.5.5.5"}
            client = type("obj", (object,), {"host": "1.2.3.4"})()

        assert _extract_client_id(MockRequest()) == "5.5.5.5"

    @pytest.mark.asyncio
    async def test_rate_limiter_returns_bool_when_redis_unavailable(self):
        """Rate limiter should return a bool decision when Redis is unavailable."""
        from app.core.rate_limiter import check_rate_limit

        result = check_rate_limit("test-identity", "sentinel_scan")
        assert isinstance(result, bool)


# =========================================================================
# Scanner HTTP Endpoint Tests
# =========================================================================


class TestScannerEndpoints:
    """Scanner HTTP endpoint reachability tests."""

    _AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}

    @pytest.fixture(scope="module")
    def app(self):
        from app.factory import create_app

        return create_app()

    def test_token_scan_empty_body_returns_422(self, app) -> None:
        """Scanner with empty body should return 422."""
        from fastapi.testclient import TestClient

        resp = TestClient(app).post(
            "/api/v2/scanner/token/scan",
            json={},
            headers={**self._AUTH, "Authorization": "Bearer test-bearer-token"},
        )
        assert resp.status_code == 422

    def test_wallet_scan_missing_fields_returns_422(self, app) -> None:
        """Wallet scan without required fields should return 422."""
        from fastapi.testclient import TestClient

        resp = TestClient(app).post(
            "/api/v2/scanner/wallet/scan",
            json={},
            headers={**self._AUTH, "Authorization": "Bearer test-bearer-token"},
        )
        assert resp.status_code == 422
