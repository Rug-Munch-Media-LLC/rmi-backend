"""Unit tests for newsletter ORM models — no database required."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domains.newsletter.models import (
    Newsletter,
    NewsletterCrosspost,
    NewsletterDelivery,
    NewsletterPayment,
    NewsletterSub,
)


class TestNewsletterModel:
    """Tests for the Newsletter model construction."""

    def test_newsletter_minimal(self):
        """Test creating a newsletter with minimal fields."""
        nl = Newsletter(
            id=uuid4(),
            creator_id="0xcreator123",
            title="Test Newsletter",
            content_html="<h1>Hello</h1><p>World</p>",
            status="draft",
            segment="all",
            total_recipients=0,
            total_opened=0,
            total_clicked=0,
            total_revenue=0.0,
        )
        assert nl.id is not None
        assert isinstance(nl.id, UUID)
        assert nl.creator_id == "0xcreator123"
        assert nl.title == "Test Newsletter"
        assert nl.status == "draft"
        assert nl.segment == "all"
        assert nl.total_recipients == 0
        assert nl.total_opened == 0
        assert nl.total_clicked == 0
        assert nl.total_revenue == 0.0

    def test_newsletter_full(self):
        """Test creating a newsletter with all fields."""
        future = datetime.now(UTC) + timedelta(hours=24)
        nl = Newsletter(
            creator_id="0xcreator",
            title="Full Newsletter",
            slug="full-newsletter",
            content_html="<p>Full content</p>",
            content_text="Full content plain",
            excerpt="A preview",
            cover_image="https://example.com/image.jpg",
            tags=["crypto", "defi", "news"],
            status="scheduled",
            publish_at=future,
            segment="paid_only",
            subject_line="Check this out",
            preheader_text="Open for details",
            recurrence="weekly",
            total_recipients=100,
            total_opened=45,
            total_clicked=12,
            total_revenue=500.0,
            ghost_post_id="ghost_abc123",
        )
        assert nl.slug == "full-newsletter"
        assert nl.status == "scheduled"
        assert nl.publish_at == future
        assert nl.segment == "paid_only"
        assert nl.recurrence == "weekly"
        assert nl.total_recipients == 100
        assert nl.total_revenue == 500.0

    def test_newsletter_slug_truncation(self):
        """Test slug can be over 200 chars (model doesn't enforce)."""
        nl = Newsletter(
            creator_id="0x",
            title="Title",
            content_html="<p>C</p>",
            slug="x" * 256,
        )
        assert len(nl.slug) == 256  # DB will truncate


class TestNewsletterSubModel:
    """Tests for the NewsletterSub model."""

    def test_subscriber_email(self):
        """Test subscriber with email."""
        sub = NewsletterSub(
            newsletter_id=UUID("00000000-0000-0000-0000-000000000001"),
            email="test@example.com",
            tier="free",
            status="active",
        )
        assert sub.email == "test@example.com"
        assert sub.tier == "free"
        assert sub.status == "active"
        assert sub.protocol is None
        assert sub.wallet_address is None

    def test_subscriber_wallet(self):
        """Test subscriber with wallet address."""
        sub = NewsletterSub(
            newsletter_id=UUID("00000000-0000-0000-0000-000000000001"),
            wallet_address="0xdeafbeefcafe",
            tier="monthly",
            status="active",
            subscription_expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        assert sub.wallet_address == "0xdeafbeefcafe"
        assert sub.tier == "monthly"
        assert sub.subscription_expires_at is not None

    def test_subscriber_protocol_identity(self):
        """Test subscriber with decentralized identity."""
        sub = NewsletterSub(
            newsletter_id=UUID("00000000-0000-0000-0000-000000000001"),
            protocol="nostr",
            protocol_id="npub1...abc123",
            tier="free",
        )
        assert sub.protocol == "nostr"
        assert sub.protocol_id == "npub1...abc123"

    def test_subscriber_token_gate(self):
        """Test subscriber with token gate holdings."""
        sub = NewsletterSub(
            newsletter_id=UUID("00000000-0000-0000-0000-000000000001"),
            email="tokens@example.com",
            token_gate_holdings={"token": "0xTOKEN", "amount": "1000000"},
            tier="free",
        )
        assert sub.token_gate_holdings["token"] == "0xTOKEN"
        assert sub.token_gate_holdings["amount"] == "1000000"

    def test_subscriber_interests(self):
        """Test subscriber interests."""
        sub = NewsletterSub(
            newsletter_id=UUID("00000000-0000-0000-0000-000000000001"),
            email="interest@example.com",
            interests=["crypto", "nfts", "defi"],
            source="widget",
            referrer="twitter",
            tier="free",
        )
        assert sub.interests == ["crypto", "nfts", "defi"]
        assert sub.source == "widget"
        assert sub.referrer == "twitter"


class TestNewsletterPaymentModel:
    """Tests for the NewsletterPayment model."""

    def test_eth_payment(self):
        """Test ETH payment record."""
        payment = NewsletterPayment(
            subscriber_id=UUID("00000000-0000-0000-0000-000000000010"),
            chain="eth",
            token="USDC",
            amount="50.00",
            usd_value=50.0,
            tx_hash="0x" + "a" * 64,
            from_address="0xpayer",
            tier="annual",
            duration_days=365,
            expires_at=datetime.now(UTC) + timedelta(days=365),
            status="confirmed",
            platform_fee_pct=10.0,
            creator_amount="45.00",
            platform_amount="5.00",
        )
        assert payment.chain == "eth"
        assert payment.token == "USDC"
        assert payment.amount == "50.00"
        assert payment.status == "confirmed"
        assert payment.creator_amount == "45.00"
        assert payment.platform_amount == "5.00"

    def test_sol_payment(self):
        """Test SOL payment record."""
        payment = NewsletterPayment(
            subscriber_id=UUID("00000000-0000-0000-0000-000000000010"),
            chain="sol",
            token="SOL",
            amount="0.5",
            tx_hash="sol_tx_abc123",
            from_address="sol_wallet_abc",
            tier="monthly",
            duration_days=30,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            status="pending",
        )
        assert payment.chain == "sol"
        assert payment.tx_hash == "sol_tx_abc123"
        assert payment.status == "pending"

    def test_lightning_payment(self):
        """Test Lightning payment record."""
        payment = NewsletterPayment(
            subscriber_id=UUID("00000000-0000-0000-0000-000000000010"),
            chain="lightning",
            token="BTC",
            amount="10000",  # sats
            tx_hash="lnbc_invoice_hash",
            from_address="ln_node_pubkey",
            tier="founding",
            duration_days=365,
            expires_at=datetime.now(UTC) + timedelta(days=365),
            status="confirmed",
        )
        assert payment.chain == "lightning"
        assert payment.tier == "founding"


class TestNewsletterDeliveryModel:
    """Tests for the NewsletterDelivery model."""

    def test_delivery_email(self):
        """Test email delivery record."""
        delivery = NewsletterDelivery(
            id=uuid4(),
            newsletter_id=UUID("00000000-0000-0000-0000-000000000020"),
            subscriber_id=UUID("00000000-0000-0000-0000-000000000030"),
            channel="email",
            status="sent",
            sent_at=datetime.now(UTC),
            open_count=0,
            click_count=0,
            retry_count=0,
        )
        assert delivery.channel == "email"
        assert delivery.status == "sent"
        assert delivery.open_count == 0

    def test_delivery_opened(self):
        """Test delivery with open tracking."""
        now = datetime.now(UTC)
        delivery = NewsletterDelivery(
            newsletter_id=UUID("00000000-0000-0000-0000-000000000020"),
            subscriber_id=UUID("00000000-0000-0000-0000-000000000030"),
            channel="email",
            status="delivered",
            sent_at=now - timedelta(hours=1),
            delivered_at=now - timedelta(minutes=30),
            opened_at=now,
            open_count=2,
            click_count=1,
            user_agent="Mozilla/5.0",
        )
        assert delivery.open_count == 2
        assert delivery.click_count == 1
        assert delivery.user_agent == "Mozilla/5.0"

    def test_delivery_failed(self):
        """Test failed delivery with error."""
        delivery = NewsletterDelivery(
            newsletter_id=UUID("00000000-0000-0000-0000-000000000020"),
            subscriber_id=UUID("00000000-0000-0000-0000-000000000030"),
            channel="email",
            status="failed",
            error_message="Connection refused",
            retry_count=3,
        )
        assert delivery.status == "failed"
        assert delivery.error_message == "Connection refused"
        assert delivery.retry_count == 3


class TestNewsletterCrosspostModel:
    """Tests for the NewsletterCrosspost model."""

    def test_ghost_crosspost(self):
        """Test ghost crosspost."""
        xp = NewsletterCrosspost(
            id=uuid4(),
            newsletter_id=UUID("00000000-0000-0000-0000-000000000040"),
            platform="ghost",
            platform_url="https://ghost.example.com/post/abc",
            platform_id="ghost_abc",
            status="published",
            published_at=datetime.now(UTC),
            view_count=0,
            engagement_count=0,
            retry_count=0,
        )
        assert xp.platform == "ghost"
        assert xp.status == "published"
        assert xp.view_count == 0

    def test_failed_crosspost(self):
        """Test failed crosspost."""
        xp = NewsletterCrosspost(
            newsletter_id=UUID("00000000-0000-0000-0000-000000000040"),
            platform="medium",
            status="failed",
            error_message="API rate limited",
            retry_count=2,
        )
        assert xp.status == "failed"
        assert "rate limited" in (xp.error_message or "").lower()
        assert xp.retry_count == 2


class TestTableNames:
    """Test that table names match Alembic migration."""

    def test_newsletter_table_name(self):
        assert Newsletter.__tablename__ == "newsletters"

    def test_subscriber_table_name(self):
        assert NewsletterSub.__tablename__ == "newsletter_subs"

    def test_payment_table_name(self):
        assert NewsletterPayment.__tablename__ == "newsletter_payments"

    def test_delivery_table_name(self):
        assert NewsletterDelivery.__tablename__ == "newsletter_deliveries"

    def test_crosspost_table_name(self):
        assert NewsletterCrosspost.__tablename__ == "newsletter_crossposts"
