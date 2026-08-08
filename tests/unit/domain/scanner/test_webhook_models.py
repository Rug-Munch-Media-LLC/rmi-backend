"""Tests for webhook models — Pydantic v2 validation.

Tests the data models for webhook subscriptions, events, deliveries,
and filters.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.domains.scanner.webhooks.models import (
    CreateWebhookRequest,
    WebhookDelivery,
    WebhookEvent,
    WebhookEventType,
    WebhookFilters,
    WebhookSubscription,
)


class TestWebhookEventType:
    """Tests for WebhookEventType enum."""

    def test_values_exist(self):
        """All expected event types should be defined."""
        assert WebhookEventType.TOKEN_DEPLOYED == "token.deployed"
        assert WebhookEventType.TOKEN_FIRST_SWAP == "token.first_swap"
        assert WebhookEventType.TOKEN_PRICE_CHANGE == "token.price_change"
        assert WebhookEventType.TOKEN_WHALE_MOVE == "token.whale_move"
        assert WebhookEventType.TOKEN_LIQUIDITY_CHANGE == "token.liquidity_change"
        assert WebhookEventType.WALLET_ACTIVITY == "wallet.activity"
        assert WebhookEventType.CHAIN_BLOCK == "chain.block"
        assert WebhookEventType.TRANSACTION_ENHANCED == "transaction.enhanced"

    def test_values_count(self):
        """Should have exactly 8 event types."""
        assert len(list(WebhookEventType)) == 8

    def test_values_method(self):
        """values() should return all string values."""
        vals = WebhookEventType.values()
        assert len(vals) == 8
        assert "token.deployed" in vals
        assert "chain.block" in vals
        assert "transaction.enhanced" in vals


class TestWebhookFilters:
    """Tests for WebhookFilters pydantic model."""

    def test_default_filters(self):
        """Default filters should have empty lists."""
        filters = WebhookFilters()
        assert filters.chains == []
        assert filters.token_addresses == []
        assert filters.min_value_usd is None
        assert filters.price_change_pct is None
        assert filters.wallet_addresses == []

    def test_with_chains(self):
        """Should accept chain filters."""
        filters = WebhookFilters(chains=["ethereum", "solana"])
        assert len(filters.chains) == 2
        assert "ethereum" in filters.chains

    def test_with_min_value(self):
        """Should accept minimum USD value."""
        filters = WebhookFilters(min_value_usd=1000.0)
        assert filters.min_value_usd == 1000.0

    def test_negative_value_raises(self):
        """Negative min_value_usd should raise validation error."""
        with pytest.raises(ValidationError):
            WebhookFilters(min_value_usd=-100.0)

    def test_price_change_above_100_raises(self):
        """Price change >100 should raise validation error."""
        with pytest.raises(ValidationError):
            WebhookFilters(price_change_pct=150.0)


class TestWebhookSubscription:
    """Tests for WebhookSubscription pydantic model."""

    def test_minimal_subscription(self):
        """Should create a subscription with required fields."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
        )
        assert sub.id == "whk:1"
        assert sub.active is True
        assert sub.created_at is not None
        assert sub.owner_id is None

    def test_invalid_url_raises(self):
        """URL without http/https prefix should raise."""
        with pytest.raises(ValidationError):
            WebhookSubscription(
                id="whk:2",
                url="example.com/webhook",
                event_types=[WebhookEventType.TOKEN_DEPLOYED],
            )

    def test_empty_event_types_raises(self):
        """Empty event_types list should raise."""
        with pytest.raises(ValidationError):
            WebhookSubscription(
                id="whk:3",
                url="https://example.com/webhook",
                event_types=[],
            )

    def test_with_full_fields(self):
        """Should accept all optional fields."""
        now = datetime.utcnow()
        sub = WebhookSubscription(
            id="whk:4",
            url="https://hooks.example.com/callback",
            event_types=[
                WebhookEventType.TOKEN_PRICE_CHANGE,
                WebhookEventType.TOKEN_WHALE_MOVE,
            ],
            filters=WebhookFilters(chains=["ethereum"], min_value_usd=50000),
            active=False,
            owner_id="user_abc123",
            last_triggered_at=now,
        )
        assert len(sub.event_types) == 2
        assert sub.filters.chains == ["ethereum"]
        assert sub.active is False
        assert sub.owner_id == "user_abc123"
        assert sub.last_triggered_at == now

    def test_url_max_length(self):
        """URL longer than 2048 should raise."""
        with pytest.raises(ValidationError):
            WebhookSubscription(
                id="whk:5",
                url=f"https://example.com/{'a' * 2048}",
                event_types=[WebhookEventType.CHAIN_BLOCK],
            )


class TestWebhookEvent:
    """Tests for WebhookEvent pydantic model."""

    def test_minimal_event(self):
        """Should create an event with just event_type."""
        event = WebhookEvent(event_type=WebhookEventType.TOKEN_DEPLOYED)
        assert event.event_type == "token.deployed"  # enum values
        assert event.id == ""
        assert event.subscription_id == ""
        assert event.payload == {}
        assert event.chain == ""

    def test_with_payload(self):
        """Should accept event payload."""
        event = WebhookEvent(
            event_type=WebhookEventType.TOKEN_WHALE_MOVE,
            payload={"token_address": "0xabc", "value_usd": 100000},
            chain="ethereum",
        )
        assert event.payload["token_address"] == "0xabc"
        assert event.chain == "ethereum"


class TestWebhookDelivery:
    """Tests for WebhookDelivery pydantic model."""

    def test_minimal_delivery(self):
        """Should create delivery with required fields."""
        delivery = WebhookDelivery(
            event_id="evt:1",
            subscription_id="whk:1",
            url="https://example.com/webhook",
        )
        assert delivery.status == "pending"
        assert delivery.attempt == 0
        assert delivery.max_attempts == 3

    def test_invalid_status_raises(self):
        """Invalid status should raise."""
        with pytest.raises(ValidationError):
            WebhookDelivery(
                event_id="evt:1",
                subscription_id="whk:1",
                url="https://example.com/webhook",
                status="invalid_status",
            )

    def test_delivery_with_response(self):
        """Should accept response code and error."""
        delivery = WebhookDelivery(
            event_id="evt:2",
            subscription_id="whk:2",
            url="https://example.com/webhook",
            status="delivered",
            attempt=1,
            response_code=200,
        )
        assert delivery.status == "delivered"
        assert delivery.response_code == 200

    def test_max_attempts_positive(self):
        """max_attempts should default to 3."""
        delivery = WebhookDelivery(
            event_id="evt:3",
            subscription_id="whk:3",
            url="https://example.com/webhook",
        )
        assert delivery.max_attempts == 3


class TestCreateWebhookRequest:
    """Tests for CreateWebhookRequest API model."""

    def test_minimal_request(self):
        """Should create with required fields."""
        req = CreateWebhookRequest(
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
        )
        assert req.filters is None

    def test_with_filters(self):
        """Should accept optional filters."""
        req = CreateWebhookRequest(
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_PRICE_CHANGE],
            filters=WebhookFilters(chains=["ethereum"]),
        )
        assert req.filters is not None
        assert req.filters.chains == ["ethereum"]

    def test_http_url_accepted(self):
        """http:// URLs should be accepted."""
        req = CreateWebhookRequest(
            url="http://localhost/webhook",
            event_types=[WebhookEventType.CHAIN_BLOCK],
        )
        assert req.url == "http://localhost/webhook"

    def test_url_required(self):
        """URL is required and must not be empty."""
        with pytest.raises(ValidationError):
            CreateWebhookRequest(
                url="",
                event_types=[WebhookEventType.CHAIN_BLOCK],
            )
