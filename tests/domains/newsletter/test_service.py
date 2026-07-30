"""Unit tests for NewsletterService — uses mocked database session."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.newsletter.schemas import (
    CryptoSubscribeRequest,
    NewsletterCreate,
    SubscriberImportRow,
)
from app.domains.newsletter.service import SUBSCRIPTION_TIERS, NewsletterService


@pytest.fixture
def mock_db() -> AsyncMock:
    """Fixture for a mocked async database session."""
    db = AsyncMock(spec=AsyncSession)
    db.flush = AsyncMock()
    return db


@pytest.fixture
def svc(mock_db: AsyncMock) -> NewsletterService:
    """Fixture for NewsletterService with mocked DB."""
    return NewsletterService(mock_db)


class TestTierConfig:
    """Tests for subscription tier configuration."""

    def test_tiers_defined(self):
        assert "free" in SUBSCRIPTION_TIERS
        assert "monthly" in SUBSCRIPTION_TIERS
        assert "annual" in SUBSCRIPTION_TIERS
        assert "founding" in SUBSCRIPTION_TIERS

    def test_free_tier(self):
        assert SUBSCRIPTION_TIERS["free"]["price_usd"] == 0
        assert SUBSCRIPTION_TIERS["free"]["duration_days"] == 0

    def test_monthly_tier(self):
        assert SUBSCRIPTION_TIERS["monthly"]["price_usd"] == 5
        assert SUBSCRIPTION_TIERS["monthly"]["duration_days"] == 30

    def test_annual_tier(self):
        assert SUBSCRIPTION_TIERS["annual"]["price_usd"] == 50
        assert SUBSCRIPTION_TIERS["annual"]["duration_days"] == 365

    def test_founding_tier(self):
        assert SUBSCRIPTION_TIERS["founding"]["price_usd"] == 100
        assert SUBSCRIPTION_TIERS["founding"]["duration_days"] == 365


class TestSlugify:
    """Tests for the slugify helper."""

    def test_basic(self):
        assert NewsletterService._slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert NewsletterService._slugify("Crypto & DeFi!") == "crypto-defi"

    def test_multiple_spaces(self):
        assert NewsletterService._slugify("  Lots   of   spaces  ") == "lots-of-spaces"

    def test_empty_string(self):
        assert NewsletterService._slugify("") == "untitled"


class TestCreateNewsletter:
    """Tests for creating newsletters."""

    @pytest.mark.asyncio
    async def test_create_draft(self, svc: NewsletterService, mock_db: AsyncMock):
        """Test creating a draft newsletter."""
        data = NewsletterCreate(
            title="Test Newsletter",
            content_html="<h1>Hello</h1>",
            tags=["crypto"],
            segment="all",
        )
        nl = await svc.create_newsletter("0xcreator", data)

        assert nl.title == "Test Newsletter"
        assert nl.creator_id == "0xcreator"
        assert nl.status == "draft"
        assert nl.segment == "all"
        assert nl.slug is not None
        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_scheduled(self, svc: NewsletterService):
        """Test creating a scheduled newsletter."""
        future = datetime.now(UTC) + timedelta(hours=24)
        data = NewsletterCreate(
            title="Scheduled",
            content_html="<p>Content</p>",
            publish_at=future,
            recurrence="weekly",
        )
        nl = await svc.create_newsletter("0xcreator", data)

        assert nl.status == "scheduled"
        assert nl.publish_at is not None
        assert nl.recurrence == "weekly"


@patch("app.domains.newsletter.services.service.verify_crypto_payment")
class TestSubscribeWithCrypto:
    """Tests for crypto subscription flow."""

    @pytest.mark.asyncio
    async def test_free_subscribe(self, mock_verify, svc: NewsletterService):
        """Test free subscription."""
        # Create a newsletter first
        from app.domains.newsletter.models import Newsletter

        nl = Newsletter(
            id=UUID("10000000-0000-0000-0000-000000000001"),
            creator_id="0xcreator",
            title="Free NL",
            content_html="<p>Free</p>",
        )

        async def _mock_get(*args, **kwargs):
            return nl

        svc.db.get = _mock_get
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        svc.db.execute = AsyncMock(return_value=mock_result)

        result = await svc.subscribe_with_crypto(
            CryptoSubscribeRequest(
                newsletter_id="10000000-0000-0000-0000-000000000001",
                email="free@example.com",
                tier="free",
                chain="eth",
                token="USDC",
                tx_hash="0x" + "a" * 64,
                from_address="0x0000000000000000000000000000000000000000",
            )
        )

        assert result.email == "free@example.com"
        assert result.tier == "free"
        assert result.status == "active"
        mock_verify.assert_not_called()

    @pytest.mark.asyncio
    async def test_paid_subscribe(self, mock_verify, svc: NewsletterService):
        """Test paid subscription with crypto."""
        mock_verify.return_value = True

        from app.domains.newsletter.models import Newsletter

        nl = Newsletter(
            id=UUID("20000000-0000-0000-0000-000000000002"),
            creator_id="0xcreator",
            title="Paid NL",
            content_html="<p>Premium</p>",
        )
        svc.db.get = AsyncMock(return_value=nl)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        svc.db.execute = AsyncMock(return_value=mock_result)

        result = await svc.subscribe_with_crypto(
            CryptoSubscribeRequest(
                newsletter_id="20000000-0000-0000-0000-000000000002",
                email="paid@example.com",
                wallet_address="0xpaidwallet",
                tier="monthly",
                chain="eth",
                token="USDC",
                tx_hash="0x" + "a" * 64,
                from_address="0xpaidwallet",
            )
        )

        assert result.tier == "monthly"
        assert result.status == "active"
        assert result.subscription_tx_hash is not None
        mock_verify.assert_called_once()


class TestImportSubscribers:
    """Tests for subscriber import."""

    @pytest.mark.asyncio
    async def test_import_valid(self, svc: NewsletterService):
        """Test importing valid subscribers."""
        from app.domains.newsletter.models import Newsletter

        nl = Newsletter(
            id=UUID("30000000-0000-0000-0000-000000000003"),
            creator_id="0xcreator",
            title="Import NL",
            content_html="<p>Import</p>",
        )
        svc.db.get = AsyncMock(return_value=nl)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        svc.db.execute = AsyncMock(return_value=mock_result)

        rows = [
            SubscriberImportRow(email="alice@example.com", tier="free"),
            SubscriberImportRow(email="bob@example.com", tier="monthly"),
            SubscriberImportRow(wallet_address="0xbob", tier="free"),
            SubscriberImportRow(protocol="nostr", protocol_id="npub1abc", tier="free"),
        ]
        result = await svc.import_subscribers("30000000-0000-0000-0000-000000000003", rows)

        assert result["imported"] == 4
        assert result["skipped"] == 0

    @pytest.mark.asyncio
    async def test_import_invalid_row(self, svc: NewsletterService):
        """Test importing a row with no identifiers."""
        from app.domains.newsletter.models import Newsletter

        nl = Newsletter(
            id=UUID("40000000-0000-0000-0000-000000000004"),
            creator_id="0xcreator",
            title="Bad Import",
            content_html="<p>Bad</p>",
        )
        svc.db.get = AsyncMock(return_value=nl)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        svc.db.execute = AsyncMock(return_value=mock_result)

        rows = [SubscriberImportRow(email=None, wallet_address=None, protocol=None, protocol_id=None)]
        result = await svc.import_subscribers("40000000-0000-0000-0000-000000000004", rows)

        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert len(result["errors"]) == 1


class TestDeliveryStatus:
    """Tests for delivery status."""

    @pytest.mark.asyncio
    async def test_empty_delivery(self, svc: NewsletterService):
        """Test delivery status with no deliveries."""
        from app.domains.newsletter.models import Newsletter

        nl = Newsletter(
            id=UUID("50000000-0000-0000-0000-000000000005"),
            creator_id="0xcreator",
            title="No deliveries",
            content_html="<p>Empty</p>",
        )
        svc.db.get = AsyncMock(return_value=nl)

        # Mock scalar queries to return 0
        svc.db.scalar = AsyncMock(return_value=0)

        status = await svc.get_delivery_status("50000000-0000-0000-0000-000000000005")

        assert status["total_sent"] == 0
        assert status["total_delivered"] == 0
        assert status["open_rate"] == 0.0
        assert status["click_rate"] == 0.0
        assert status["delivery_rate"] == 0.0


class TestExportSubscribers:
    """Tests for CSV export."""

    @pytest.mark.asyncio
    async def test_export_empty(self, svc: NewsletterService):
        """Test exporting with no subscribers."""
        # Mock list_subscribers to return empty
        # Mock db to return empty subscriber list
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        svc.db.execute = AsyncMock(return_value=mock_result)
        svc.db.scalar = AsyncMock(return_value=0)
        csv_data = await svc.export_subscribers_csv("50000000-0000-0000-0000-000000000005")
        assert "email,wallet_address,protocol" in csv_data


class TestSchedule:
    """Tests for scheduling logic."""

    @pytest.mark.asyncio
    async def test_schedule_future(self, svc: NewsletterService):
        """Test scheduling a newsletter in the future."""
        from app.domains.newsletter.models import Newsletter

        nl = Newsletter(
            id=UUID("60000000-0000-0000-0000-000000000006"),
            creator_id="0xcreator",
            title="Schedule",
            content_html="<p>Future</p>",
            status="draft",
        )
        svc.db.get = AsyncMock(return_value=nl)

        future = datetime.now(UTC) + timedelta(hours=48)
        result = await svc.schedule_newsletter(
            "60000000-0000-0000-0000-000000000006",
            publish_at=future,
        )

        assert result["status"] == "scheduled"
        assert nl.status == "scheduled"
        assert nl.publish_at == future

    @pytest.mark.asyncio
    async def test_schedule_past_fails(self, svc: NewsletterService):
        """Test scheduling in the past raises error."""
        from app.core.errors import ValidationError
        from app.domains.newsletter.models import Newsletter

        nl = Newsletter(
            id=UUID("70000000-0000-0000-0000-000000000007"),
            creator_id="0xcreator",
            title="Past",
            content_html="<p>Past</p>",
            status="draft",
        )
        svc.db.get = AsyncMock(return_value=nl)

        past = datetime.now(UTC) - timedelta(hours=1)
        with pytest.raises(ValidationError):
            await svc.schedule_newsletter(
                "70000000-0000-0000-0000-000000000007",
                publish_at=past,
            )
