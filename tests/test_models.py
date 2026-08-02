"""Pydantic & ORM model unit tests — 15+ tests covering request/response
model validation, serialization, deserialization, and edge cases.

Focuses on router Pydantic models (request bodies, response schemas)
and ORM model field constraints.
"""

from __future__ import annotations

import os

# Force env vars BEFORE any app imports to satisfy Settings validation
os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"  # override .env default 'development' which is invalid

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

# ── Router Pydantic models ──────────────────────────────────────────


class TestTokenScanRequest:
    """TokenScanRequest from unified_scanner_router.py."""

    def test_valid_request(self):
        from app.routers.unified_scanner_router import TokenScanRequest

        req = TokenScanRequest(
            address="So11111111111111111111111111111111111111112", chain="solana"
        )
        assert req.address == "So11111111111111111111111111111111111111112"
        assert req.chain == "solana"

    def test_default_chain(self):
        from app.routers.unified_scanner_router import TokenScanRequest

        req = TokenScanRequest(address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
        assert req.chain == "solana"

    def test_address_required(self):
        from app.routers.unified_scanner_router import TokenScanRequest

        with pytest.raises(ValidationError):
            TokenScanRequest()

    def test_empty_address_allowed(self):
        """Address field is a string, empty string is technically valid."""
        from app.routers.unified_scanner_router import TokenScanRequest

        req = TokenScanRequest(address="")
        assert req.address == ""


class TestWalletScanRequest:
    """WalletScanRequest from unified_scanner_router.py."""

    def test_valid_request(self):
        from app.routers.unified_scanner_router import WalletScanRequest

        req = WalletScanRequest(address="7EcD8nUjbQiv7qJgGqgfQk7KjVgDqLXq8N5N5n5N5n5N")
        assert req.address == "7EcD8nUjbQiv7qJgGqgfQk7KjVgDqLXq8N5N5n5N5n5N"

    def test_default_chain(self):
        from app.routers.unified_scanner_router import WalletScanRequest

        req = WalletScanRequest(address="0xabc")
        assert req.chain == "solana"


class TestPryscraperBridgeModels:
    """ScrapeRequest, ExtractRequest, NewsSearchRequest from pryscraper_bridge.py."""

    def test_scrape_request_url_required(self):
        from app.routers.pryscraper_bridge import ScrapeRequest

        with pytest.raises(ValidationError):
            ScrapeRequest()

    def test_scrape_request_defaults(self):
        from app.routers.pryscraper_bridge import ScrapeRequest

        req = ScrapeRequest(url="https://example.com")
        assert req.timeout == 30
        assert req.only_main_content is True
        assert req.bypass_cloudflare is True
        assert req.js_render is False

    def test_scrape_request_field_types(self):
        from app.routers.pryscraper_bridge import ScrapeRequest

        req = ScrapeRequest(
            url="https://example.com",
            formats=["markdown", "html"],
            only_main_content=False,
            timeout=60,
            bypass_cloudflare=False,
            js_render=True,
        )
        assert isinstance(req.url, str)
        assert isinstance(req.formats, list)
        assert isinstance(req.only_main_content, bool)
        assert isinstance(req.timeout, int)

    def test_scrape_request_timeout_min(self):
        from app.routers.pryscraper_bridge import ScrapeRequest

        with pytest.raises(ValidationError):
            ScrapeRequest(url="https://example.com", timeout=1)

    def test_scrape_request_timeout_max(self):
        from app.routers.pryscraper_bridge import ScrapeRequest

        with pytest.raises(ValidationError):
            ScrapeRequest(url="https://example.com", timeout=999)

    def test_extract_request_url_required(self):
        from app.routers.pryscraper_bridge import ExtractRequest

        with pytest.raises(ValidationError):
            ExtractRequest()

    def test_extract_request_optional_schema(self):
        from app.routers.pryscraper_bridge import ExtractRequest

        req = ExtractRequest(url="https://example.com", json_schema={"title": "string"})
        assert req.json_schema == {"title": "string"}

    def test_extract_request_schema_can_be_none(self):
        from app.routers.pryscraper_bridge import ExtractRequest

        req = ExtractRequest(url="https://example.com")
        assert req.json_schema is None

    def test_news_search_request_query_required(self):
        from app.routers.pryscraper_bridge import NewsSearchRequest

        with pytest.raises(ValidationError):
            NewsSearchRequest()

    def test_news_search_request_default_limit(self):
        from app.routers.pryscraper_bridge import NewsSearchRequest

        req = NewsSearchRequest(query="bitcoin")
        assert req.limit == 10

    def test_news_search_request_limit_range(self):
        from app.routers.pryscraper_bridge import NewsSearchRequest

        req = NewsSearchRequest(query="bitcoin", limit=50)
        assert req.limit == 50

        # Too low
        with pytest.raises(ValidationError):
            NewsSearchRequest(query="bitcoin", limit=0)

        # Too high
        with pytest.raises(ValidationError):
            NewsSearchRequest(query="bitcoin", limit=100)


# ═══════════════════════════════════════════════════════════════════════
# ── ORM model field tests
# ═══════════════════════════════════════════════════════════════════════


class TestTokenModel:
    """Token ORM model field types and defaults."""

    def test_column_types(self):
        """Token SQLAlchemy columns have correct types."""
        from sqlalchemy import inspect

        from app.models.token import Token

        mapper = inspect(Token)
        columns = {c.name: c for c in mapper.columns}

        assert columns["address"].type.python_type is str
        assert columns["chain"].type.python_type is str
        assert columns["decimals"].type.python_type is int
        assert columns["total_supply"].type.python_type is float
        assert columns["verified"].type.python_type is bool

    def test_defaults_defined_on_model(self):
        """Token model columns have correct default values defined."""
        from app.models.token import Token

        columns = {c.name: c for c in Token.__table__.c}
        # Columns with Python-side defaults
        assert columns["chain"].default is not None
        assert columns["decimals"].default is not None
        assert columns["risk_level"].default is not None
        # id uses uuid4 default
        assert columns["id"].default is not None

    def test_explicit_construction(self):
        """Token accepts explicit field values."""
        from app.models.token import Token

        token = Token(
            address="0xabc123",
            chain="ethereum",
            name="Test Token",
            symbol="TEST",
            decimals=18,
            total_supply=1_000_000.0,
            holders=500,
            verified=True,
            risk_score=25,
            risk_level="medium",
        )
        assert isinstance(token.address, str)
        assert isinstance(token.decimals, int)
        assert isinstance(token.verified, bool)
        assert token.name == "Test Token"
        assert token.risk_level == "medium"

    def test_extra_default_none(self):
        from app.models.token import Token

        token = Token()
        assert token.extra is None

    def test_field_types(self):
        from app.models.token import Token

        token = Token(
            address="0xabc123",
            chain="ethereum",
            name="Test Token",
            symbol="TEST",
            decimals=18,
            total_supply=1_000_000.0,
            holders=500,
            verified=True,
            risk_score=25,
            risk_level="medium",
        )
        assert isinstance(token.address, str)
        assert isinstance(token.chain, str)
        assert isinstance(token.decimals, int)
        assert isinstance(token.total_supply, float)
        assert isinstance(token.holders, int)
        assert isinstance(token.verified, bool)
        assert isinstance(token.risk_score, int)
        assert isinstance(token.risk_level, str)


class TestScanResultModel:
    """ScanResult ORM model field types and construction."""

    def test_column_types(self):
        """ScanResult columns have correct Python types."""
        from sqlalchemy import inspect

        from app.models.scan import ScanResult

        mapper = inspect(ScanResult)
        cols = {c.name: c for c in mapper.columns}
        assert cols["safety_score"].type.python_type is int
        assert cols["risk_score"].type.python_type is int
        assert cols["confidence"].type.python_type is int

    def test_explicit_construction(self):
        """ScanResult accepts explicit field values."""
        from app.models.scan import ScanResult

        sr = ScanResult(
            token_address="0xabc",
            chain="solana",
            risk_flags=["honeypot", "high_tax"],
            modules_run=["tier1"],
            tier_required="premium",
        )
        assert sr.token_address == "0xabc"
        assert "honeypot" in sr.risk_flags
        assert sr.tier_required == "premium"


class TestUserModel:
    """User ORM model field types and construction."""

    def test_column_types(self):
        """User columns have correct types."""
        from sqlalchemy import inspect

        from app.models.user import User

        mapper = inspect(User)
        cols = {c.name: c for c in mapper.columns}
        assert cols["role"].type.python_type is str
        assert cols["xp"].type.python_type is int
        assert cols["level"].type.python_type is int

    def test_role_assignment(self):
        from app.models.user import User

        user = User(role="ADMIN")
        assert user.role == "ADMIN"


class TestLabelModel:
    """Label ORM model field types and construction."""

    def test_column_types(self):
        """Label columns have correct types."""
        from sqlalchemy import inspect

        from app.models.label import Label

        mapper = inspect(Label)
        cols = {c.name: c for c in mapper.columns}
        assert cols["confidence"].type.python_type is float

    def test_explicit_construction(self):
        """Label accepts explicit field values."""
        from app.models.label import Label

        label = Label(
            address="0xabc",
            chain="ethereum",
            label_type="exchange",
            label_value="binance",
            source="manual",
            confidence=0.95,
            entity="Binance",
        )
        assert label.address == "0xabc"
        assert label.label_type == "exchange"
        assert label.confidence == 0.95
        assert label.entity == "Binance"


class TestPaymentModel:
    """Payment ORM model field construction."""

    def test_explicit_construction(self):
        from app.models.payment import Payment

        pmt = Payment(tool="token_scan", chain="solana", tx_hash="abc123", status="confirmed")
        assert pmt.tool == "token_scan"
        assert pmt.status == "confirmed"
        assert pmt.tx_hash == "abc123"


class TestAlertModel:
    """Alert ORM model field construction."""

    def test_explicit_construction(self):
        from app.models.alert import Alert

        now = datetime.now(UTC)
        alert = Alert(
            token_address="0xabc",
            chain="solana",
            alert_type="price_drop",
            severity="critical",
            payload={"drop_pct": 15},
            fired_at=now,
        )
        assert alert.token_address == "0xabc"
        assert alert.alert_type == "price_drop"
        assert alert.severity == "critical"
        assert alert.payload == {"drop_pct": 15}


class TestAlertSubscriptionModel:
    """AlertSubscription ORM model field construction."""

    def test_explicit_construction(self):
        from app.models.alert import AlertSubscription

        sub = AlertSubscription(
            token_address="0xabc",
            chain="solana",
            alert_types=["price_drop", "liquidity_change"],
            webhook_url="https://example.com/hook",
            active=False,
        )
        assert sub.token_address == "0xabc"
        assert "price_drop" in sub.alert_types
        assert sub.webhook_url == "https://example.com/hook"
        assert sub.active is False


# ═══════════════════════════════════════════════════════════════════════
# ── Router Pydantic models (additional routers)
# ═══════════════════════════════════════════════════════════════════════


class TestAddressProfilerModel:
    """AddressProfile model from address_profiler.py."""

    def test_valid_profile(self):
        from app.routers.address_profiler import AddressProfile

        profile = AddressProfile(
            address="0xabc",
            labels=[{"source": "arkham", "label": "exchange"}],
            total_value_usd=10000.50,
            risk_score=15,
            risk_factors=["high_tx_velocity"],
        )
        assert profile.address == "0xabc"
        assert profile.risk_score == 15
        assert len(profile.labels) == 1
        assert profile.total_value_usd == 10000.50

    def test_default_fields(self):
        from app.routers.address_profiler import AddressProfile

        profile = AddressProfile(address="0xabc")
        assert profile.address == "0xabc"
        assert profile.risk_score == 50  # default


class TestDeathClockModel:
    """DeathClockResult model from death_clock.py."""

    def test_valid_result(self):
        from app.routers.death_clock import DeathClockResult

        result = DeathClockResult(
            token_address="0xabc",
            chain="solana",
            symbol="TEST",
            predicted_days=30.0,
            risk_level="HIGH",
            confidence=0.85,
            factors={"low_liquidity": -0.5, "age_days": 0.3},
            explanation="Test explanation",
        )
        assert result.token_address == "0xabc"
        assert result.risk_level == "HIGH"
        assert result.confidence == 0.85
        assert result.factors["low_liquidity"] == -0.5


class TestTokenCVModel:
    """TokenCV model for talent/team scoring."""

    def test_valid_token_cv(self):
        from app.routers.token_cv import TokenCV

        cv = TokenCV(
            token_address="0xabc",
            chain="ethereum",
            symbol="TEST",
            name="Test Token",
            age_days=30,
            holders=500,
            liquidity_usd=100000.0,
            volume_24h=50000.0,
            price_usd=0.05,
            market_cap_usd=5000000.0,
            safety_score=75,
            risk_flags=["low_liquidity"],
            security={"audit": "passed"},
            social={"twitter_followers": 1000},
            deployer={"address": "0xdeployer", "tokens_created": 5},
            summary="Token CV summary",
        )
        assert cv.token_address == "0xabc"
        assert cv.safety_score == 75
        assert len(cv.risk_flags) == 1
        assert cv.summary == "Token CV summary"


class TestCrossChainListingModel:
    """CrossChainListing model from chain_comparability.py."""

    def test_valid_listing(self):
        from app.routers.chain_comparability import CrossChainListing

        listing = CrossChainListing(
            chain="ethereum",
            price_usd=0.05,
            liquidity_usd=100000.0,
            volume_24h=50000.0,
            dex="uniswap",
        )
        assert listing.chain == "ethereum"
        assert listing.price_usd == 0.05
        assert listing.volume_liq_ratio == 0  # default
        assert listing.suspicious is False  # default

    def test_suspicious_listing(self):
        from app.routers.chain_comparability import CrossChainListing

        listing = CrossChainListing(
            chain="polygon",
            price_usd=0.10,
            liquidity_usd=1000.0,
            volume_24h=50000.0,
            dex="quickswap",
            suspicious=True,
            suspicion_reason="wash trading detected",
        )
        assert listing.suspicious is True
        assert listing.suspicion_reason == "wash trading detected"


class TestRugMapsRequestModel:
    """RugMapsRequest from rugmaps_router.py."""

    def test_valid_request(self):
        from app.routers.rugmaps_router import RugMapsRequest

        req = RugMapsRequest(center_wallet="0xwallet123")
        assert req.center_wallet == "0xwallet123"
        assert req.depth == 2  # default
        assert req.min_strength == 0.1  # default

    def test_custom_depth(self):
        from app.routers.rugmaps_router import RugMapsRequest

        req = RugMapsRequest(center_wallet="0xwallet123", depth=3, min_strength=0.5)
        assert req.depth == 3
        assert req.min_strength == 0.5


class TestWhaleAlertModel:
    """WhaleAlert from whale_alerts.py."""

    def test_valid_alert(self):
        from app.routers.whale_alerts import WhaleAlert

        alert = WhaleAlert(
            user_id="user_abc",
            address="0xwhale_wallet",
            chain="solana",
            threshold_usd=50000.0,
            notify_telegram=True,
            label="mega_whale",
        )
        assert alert.user_id == "user_abc"
        assert alert.address == "0xwhale_wallet"
        assert alert.threshold_usd == 50000.0
        assert alert.notify_telegram is True

    def test_default_threshold(self):
        from app.routers.whale_alerts import WhaleAlert

        alert = WhaleAlert(user_id="u1", address="0xabc", chain="ethereum")
        assert alert.threshold_usd == 100000


class TestLPHealthModel:
    """LPHealthResult from lp_health.py."""

    def test_valid_result(self):
        from app.routers.lp_health import LPHealthResult

        result = LPHealthResult(
            token_address="0xabc",
            chain="solana",
            health_score=75,
            risk_level="MEDIUM",
            locked_pct=85.0,
        )
        assert result.health_score == 75
        assert result.risk_level == "MEDIUM"


class TestProtectionResponseModel:
    """ProtectionResponse from protection.py."""

    def test_valid_response(self):
        from app.routers.protection import ProtectionResponse

        resp = ProtectionResponse(
            risk_level="SAFE",
            risk_score=10,
            checks_passed=5,
        )
        assert resp.risk_level == "SAFE"
        assert resp.risk_score == 10
