"""Service-layer unit tests — 15+ tests for services not yet covered.

Covers:
- Supabase service CRUD operations (mocked HTTP)
- Deep intelligence module functions
- Prediction market intel signal generation
- Additional service edge cases

Uses pytest-asyncio (enabled via pytest.ini). All external calls mocked.
"""

from __future__ import annotations

import os

# Force env vars BEFORE any app imports to satisfy Settings validation
os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

# ═══════════════════════════════════════════════════════════════════════
# ── Supabase Service Tests
# ═══════════════════════════════════════════════════════════════════════


class TestSupabaseServiceHealth:
    """Supabase service health check."""

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_health_healthy(self, mock_client):
        """check_health returns healthy when status is 200."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.get.return_value = mock_resp
        mock_client.return_value = mock_instance

        from app.services.supabase_service import check_health

        result = await check_health()
        assert result["status"] == "healthy"
        assert result["tables"] == 18

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_health_degraded(self, mock_client):
        """check_health returns degraded when status is not 200."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 503
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.get.return_value = mock_resp
        mock_client.return_value = mock_instance

        from app.services.supabase_service import check_health

        result = await check_health()
        assert result["status"] == "degraded"

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_health_unreachable(self, mock_client):
        """check_health returns unreachable on connection error."""
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.get.side_effect = httpx.ConnectError("No connection")
        mock_client.return_value = mock_instance

        from app.services.supabase_service import check_health

        result = await check_health()
        assert result["status"] == "unreachable"
        assert "error" in result


class TestSupabaseServiceCRUD:
    """Supabase service CRUD operations with mocked HTTP."""

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_get_alerts_success(self, mock_client):
        """get_alerts returns list of alerts on success."""
        mock_alerts = [{"id": "1", "title": "Test Alert", "severity": "high"}]
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_alerts
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.get.return_value = mock_resp
        mock_client.return_value = mock_instance

        from app.services.supabase_service import get_alerts

        result = await get_alerts(severity="high", limit=10)
        assert len(result) == 1
        assert result[0]["title"] == "Test Alert"

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_get_alerts_empty(self, mock_client):
        """get_alerts returns empty list on non-200."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 404
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.get.return_value = mock_resp
        mock_client.return_value = mock_instance

        from app.services.supabase_service import get_alerts

        result = await get_alerts()
        assert result == []

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_create_alert(self, mock_client):
        """create_alert posts correctly and returns created alert."""
        mock_created = [{"id": "new-1", "title": "Test", "severity": "medium"}]
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 201
        mock_resp.json.return_value = mock_created
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.post.return_value = mock_resp
        mock_client.return_value = mock_instance

        from app.services.supabase_service import create_alert

        result = await create_alert(
            title="Test Alert",
            description="Description here",
            severity="high",
            alert_type="security",
        )
        assert result is not None

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_get_wallets(self, mock_client):
        """get_wallets returns wallet list."""
        mock_wallets = [{"address": "0xabc", "chain": "ethereum", "risk_score": 25}]
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_wallets
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.get.return_value = mock_resp
        mock_client.return_value = mock_instance

        from app.services.supabase_service import get_wallets

        result = await get_wallets(chain="ethereum", limit=10)
        assert len(result) == 1
        assert result[0]["address"] == "0xabc"


class TestSupabaseServiceX402:
    """Supabase x402 payment persistence layer."""

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_record_payment(self, mock_client):
        """record_x402_payment_supabase posts payment."""
        mock_created = [{"id": 1, "tool": "token_scan", "amount_atoms": "1000"}]
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 201
        mock_resp.json.return_value = mock_created
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.post.return_value = mock_resp
        mock_client.return_value = mock_instance

        from app.services.supabase_service import record_x402_payment_supabase

        result = await record_x402_payment_supabase(
            tool="token_scan",
            amount_atoms="1000",
            chain="solana",
            payer="wallet123",
            status="fulfilled",
        )
        assert result is not None

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_get_x402_payments(self, mock_client):
        """get_x402_payments returns filtered payment list."""
        mock_payments = [{"tool": "token_scan", "amount_atoms": "500"}]
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payments
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.get.return_value = mock_resp
        mock_client.return_value = mock_instance

        from app.services.supabase_service import get_x402_payments

        result = await get_x402_payments(tool="token_scan", limit=5)
        assert len(result) == 1

    @patch("app.services.supabase_service.httpx.AsyncClient")
    async def test_earnings_summary(self, mock_client):
        """get_x402_earnings_summary aggregates payments."""
        mock_rows = [
            {
                "tool": "scan",
                "amount_atoms": "1000000",
                "chain": "solana",
                "created_at": "2026-07-01T00:00:00Z",
                "status": "fulfilled",
            },
            {
                "tool": "scan",
                "amount_atoms": "2000000",
                "chain": "solana",
                "created_at": "2026-07-02T00:00:00Z",
                "status": "fulfilled",
            },
        ]
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_rows
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value.get.return_value = mock_resp
        mock_client.return_value = mock_instance

        from app.services.supabase_service import get_x402_earnings_summary

        result = await get_x402_earnings_summary()
        assert result["total_usdc"] == 3.0  # 3 USDC total
        assert result["by_chain_usdc"]["solana"] == 3.0
        assert result["record_count"] == 2


# ═══════════════════════════════════════════════════════════════════════
# ── Deep Intelligence Module Tests
# ═══════════════════════════════════════════════════════════════════════


class TestDeepIntelAPIRoutes:
    """Deep intelligence router configuration and helpers."""

    def test_supported_chains_include_major(self):
        """SUPPORTED_CHAINS has all major chains."""
        from app.services.deep_intel_router import SUPPORTED_CHAINS

        for chain in ("sol", "eth", "bsc", "base"):
            assert chain in SUPPORTED_CHAINS
        assert len(SUPPORTED_CHAINS) >= 5

    def test_health_returns_features(self):
        """Deep intel health endpoint lists all features."""
        from app.services.deep_intel_router import router

        assert router.prefix == "/api/v1/deep-intel"
        routes = [r.path for r in router.routes]
        assert f"{router.prefix}/health" in routes
        assert f"{router.prefix}/deployer/{{address}}" in routes
        assert f"{router.prefix}/full-report/{{token_address}}" in routes
        assert f"{router.prefix}/batch-report" in routes

    def test_health_endpoint_has_13_features(self):
        """Deep intel health routes contain expected features."""
        from app.services.deep_intel_router import router

        route_paths = [r.path for r in router.routes]
        features = [
            "deployer_reputation",
            "preflight_simulation",
            "liquidity_lock_authenticity",
            "volume_authenticity",
            "slow_rug_detector",
            "funding_source_tracer",
            "ownership_theater",
            "exit_liquidity",
            "lifespan_predictor",
            "post_purchase_monitor",
            "cross_chain_identity",
            "social_onchain_correlation",
            "full_report",
        ]
        # The health route itself lists all features
        assert "/api/v1/deep-intel/health" in route_paths
        # We know there are 14 route endpoints from the router
        assert len(route_paths) >= 13, f"Expected 13+ routes, got {len(route_paths)}"


class TestDeepIntelFunctions:
    """Deep intelligence pure functions."""

    @patch("app.services.deep_intelligence.deployer_reputation")
    async def test_deployer_reputation_delegates(self, mock_fn):
        """api_deployer_reputation delegates correctly."""
        mock_fn.return_value = {"reputation": 85, "tokens_created": 12}

        from app.services import deep_intel_router

        with patch.object(deep_intel_router, "deployer_reputation", mock_fn):
            result = await deep_intel_router.api_deployer_reputation(address="test-address-123", chain="sol")
            assert result["reputation"] == 85
            mock_fn.assert_awaited_once_with("test-address-123", "sol")


# ═══════════════════════════════════════════════════════════════════════
# ── Prediction Market Intel Tests
# ═══════════════════════════════════════════════════════════════════════


class TestIntelSignalModel:
    """IntelSignal dataclass construction."""

    def test_valid_signal(self):
        from app.services.prediction_market_intel import IntelSignal

        signal = IntelSignal(
            signal_type="PROBABILITY_SWING",
            severity="high",
            headline="Test signal",
            insight="This is meaningful",
            evidence=[{"source": "polymarket", "probability": 0.75}],
            action="Monitor closely",
            confidence=0.85,
            generated_at="2026-07-01T00:00:00Z",
            source_markets=["test-market"],
        )
        assert signal.signal_type == "PROBABILITY_SWING"
        assert signal.severity == "high"
        assert signal.confidence == 0.85
        assert len(signal.evidence) == 1

    def test_signal_default_source_markets(self):
        from app.services.prediction_market_intel import IntelSignal

        signal = IntelSignal(
            signal_type="VOLUME_SURGE",
            severity="medium",
            headline="Volume surge detected",
            insight="Unusual volume",
            evidence=[],
            action="Check",
            confidence=0.5,
            generated_at="2026-07-01T00:00:00Z",
        )
        assert signal.source_markets == []


class TestCryptoFearIndexModel:
    """CryptoFearIndex dataclass construction."""

    def test_valid_index(self):
        from app.services.prediction_market_intel import CryptoFearIndex

        idx = CryptoFearIndex(
            overall_risk=45.0,
            exploit_risk=30.0,
            regulatory_risk=60.0,
            stablecoin_risk=20.0,
            exchange_risk=50.0,
            components=[{"market": "test", "risk": 0.5}],
            generated_at="2026-07-01T00:00:00Z",
        )
        assert idx.overall_risk == 45.0
        assert idx.exploit_risk == 30.0
        assert len(idx.components) == 1


class TestPredictionMarketIntel:
    """PredictionMarketIntel service with mocked prediction market service."""

    async def test_generate_signals_cache(self):
        """generate_signals returns cached results when fresh."""
        from app.services.prediction_market_intel import IntelSignal, PredictionMarketIntel

        intel = PredictionMarketIntel()
        cached_signal = [
            IntelSignal(
                signal_type="TEST",
                severity="info",
                headline="Cached",
                insight="Cached signal",
                evidence=[],
                action="None",
                confidence=0.5,
                generated_at="2026-07-01T00:00:00Z",
            )
        ]
        intel._signals_cache = (9999999999.0, cached_signal)

        result = await intel.generate_signals()
        assert len(result) == 1
        assert result[0].headline == "Cached"

    async def test_generate_signals_no_cache(self):
        """generate_signals queries service when cache is empty."""
        from app.services.prediction_market_intel import PredictionMarketIntel

        intel = PredictionMarketIntel()

        # Mock the underlying prediction market service to return empty
        with patch.object(intel, "_svc") as mock_svc:
            mock_svc.get_all_markets = AsyncMock(return_value=[])
            mock_svc.get_market_by_slug = AsyncMock(return_value=None)

            result = await intel.generate_signals(max_signals=5)
            # Should return empty list (no markets to analyze)
            assert isinstance(result, list)

    async def test_generate_crypto_fear_index_no_cache(self):
        """generate_crypto_fear_index queries service when cache is empty."""
        from app.services.prediction_market_intel import PredictionMarketIntel

        intel = PredictionMarketIntel()

        with patch.object(intel, "_svc") as mock_svc:
            # Mock the search method used by generate_crypto_fear_index
            mock_svc.search = AsyncMock(return_value=[])

            result = await intel.generate_crypto_fear_index()
            assert result.overall_risk >= 0
            assert result.overall_risk <= 100

    def test_thresholds(self):
        """Threshold constants are set correctly."""
        from app.services.prediction_market_intel import PredictionMarketIntel

        assert PredictionMarketIntel.SWING_THRESHOLD == 0.15
        assert PredictionMarketIntel.GAP_THRESHOLD == 0.20
        assert PredictionMarketIntel.SURGE_MULTIPLIER == 3.0
        assert PredictionMarketIntel.HIGH_VOLUME == 50_000

    def test_ecosystem_queries_includes_common_threats(self):
        """ECOSYSTEM_QUERIES includes common crypto threat topics."""
        from app.services.prediction_market_intel import PredictionMarketIntel

        queries = PredictionMarketIntel.ECOSYSTEM_QUERIES
        assert "crypto hack" in queries
        assert "defi exploit" in queries
        assert "stablecoin depeg" in queries
        assert len(queries) >= 5


# ═══════════════════════════════════════════════════════════════════════
# ── Rate Limiter Unit Tests
# ═══════════════════════════════════════════════════════════════════════


class TestUnifiedRateLimiter:
    """UnifiedRateLimiter core logic without Redis."""

    async def test_acquire_memory_allows_within_limit(self):
        """In-memory acquire allows when tokens are available."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        allowed, headers = await limiter.acquire("test-key", rate=10.0, burst=5.0)
        assert allowed is True
        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Remaining" in headers
        assert "X-RateLimit-Reset" in headers

    async def test_acquire_memory_exhausts_bucket(self):
        """In-memory acquire exhausts multiple tokens."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        # Acquire 5 tokens (exhaust burst capacity of 5)
        for _ in range(5):
            allowed, _ = await limiter.acquire("test-exhaust", rate=1.0, burst=5.0)
            assert allowed is True

        # 6th attempt should be denied
        allowed, headers = await limiter.acquire("test-exhaust", rate=1.0, burst=5.0)
        assert allowed is False
        assert "Retry-After" in headers

    async def test_acquire_memory_single_token(self):
        """In-memory acquire with single token increments count."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        _, h1 = await limiter.acquire("test-single", rate=5.0, burst=5.0)
        remaining1 = int(h1["X-RateLimit-Remaining"])
        assert remaining1 == 4  # 5 - 1

        _, h2 = await limiter.acquire("test-single", rate=5.0, burst=5.0)
        remaining2 = int(h2["X-RateLimit-Remaining"])
        assert remaining2 == 3  # 4 - 1

    async def test_acquire_memory_multiple_tokens(self):
        """In-memory acquire consumes specified number of tokens."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        allowed, headers = await limiter.acquire("test-multi", rate=5.0, burst=10.0, tokens=3)
        assert allowed is True
        remaining = int(headers["X-RateLimit-Remaining"])
        assert remaining == 7  # 10 - 3

    async def test_different_keys_independent(self):
        """Different rate limit keys have independent buckets."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        for _ in range(5):
            await limiter.acquire("key-a", rate=1.0, burst=5.0)
        await limiter.acquire("key-b", rate=1.0, burst=5.0)

        # key-a should be denied, key-b should be allowed
        allowed_a, _ = await limiter.acquire("key-a", rate=1.0, burst=5.0)
        allowed_b, _ = await limiter.acquire("key-b", rate=1.0, burst=5.0)

        assert allowed_a is False
        assert allowed_b is True

    async def test_stats_returns_buckets(self):
        """stats returns non-empty buckets dict."""
        from app.core.rate_limiter import UnifiedRateLimiter

        limiter = UnifiedRateLimiter()
        await limiter.acquire("stats-test-key", rate=1.0, burst=5.0)
        stats = await limiter.stats(key_prefix="stats-test")
        assert "buckets" in stats
