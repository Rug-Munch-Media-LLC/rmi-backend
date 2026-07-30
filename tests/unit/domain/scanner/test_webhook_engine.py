"""Tests for webhook engine — filter matching, cursor building, subscription map.

Tests the WebhookEngine's internal logic without requiring Redis or
external services.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.webhooks.engine import WebhookEngine
from app.domains.scanner.webhooks.models import (
    WebhookEventType,
    WebhookFilters,
    WebhookSubscription,
)


class TestMatchesFilters:
    """Tests for _matches_filters — subscription filter matching."""

    def setup_method(self):
        self.engine = WebhookEngine()

    def test_no_filters_matches_always(self):
        """Subscription with no filters should match any event."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(),
        )
        assert self.engine._matches_filters({"chain": "ethereum"}, sub) is True
        assert self.engine._matches_filters({"chain": "solana"}, sub) is True
        assert self.engine._matches_filters({}, sub) is True

    def test_chain_filter_matches(self):
        """Event chain matching subscription chain should match."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(chains=["ethereum"]),
        )
        assert self.engine._matches_filters({"chain": "ethereum"}, sub) is True

    def test_chain_filter_mismatch(self):
        """Event chain not matching subscription chain should not match."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(chains=["ethereum"]),
        )
        assert self.engine._matches_filters({"chain": "solana"}, sub) is False

    def test_chain_filter_case_insensitive(self):
        """Chain filter should be case-insensitive."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(chains=["Ethereum"]),
        )
        assert self.engine._matches_filters({"chain": "ethereum"}, sub) is True

    def test_token_address_filter_matches(self):
        """Event token matching subscription token should match."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(token_addresses=["0xabc"]),
        )
        assert self.engine._matches_filters({"token_address": "0xabc"}, sub) is True

    def test_token_address_filter_mismatch(self):
        """Event token not matching subscription token should not match."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(token_addresses=["0xabc"]),
        )
        assert self.engine._matches_filters({"token_address": "0xdef"}, sub) is False

    def test_value_filter_with_sufficient_value(self):
        """Event value above min_value_usd should match."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_WHALE_MOVE],
            filters=WebhookFilters(min_value_usd=1000.0),
        )
        assert self.engine._matches_filters({"value_decimal": 5000.0}, sub) is True

    def test_value_filter_with_insufficient_value(self):
        """Event value below min_value_usd should not match."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_WHALE_MOVE],
            filters=WebhookFilters(min_value_usd=1000.0),
        )
        assert self.engine._matches_filters({"value_decimal": 100.0}, sub) is False

    def test_value_filter_with_no_value(self):
        """Event with no value_decimal defaults to 0, which fails min_value_usd >=1000."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(min_value_usd=1000.0),
        )
        # value_decimal defaults to 0 when missing, and 0 < 1000, so filter fails
        assert self.engine._matches_filters({"chain": "ethereum"}, sub) is False

    def test_combined_filters_all_match(self):
        """All filters matching should result in match."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_WHALE_MOVE],
            filters=WebhookFilters(
                chains=["ethereum"],
                token_addresses=["0xabc"],
                min_value_usd=1000.0,
            ),
        )
        event = {"chain": "ethereum", "token_address": "0xabc", "value_decimal": 5000.0}
        assert self.engine._matches_filters(event, sub) is True

    def test_combined_filters_one_fails(self):
        """One filter failing should result in no match."""
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_WHALE_MOVE],
            filters=WebhookFilters(
                chains=["ethereum"],
                token_addresses=["0xabc"],
                min_value_usd=1000.0,
            ),
        )
        event = {"chain": "solana", "token_address": "0xabc", "value_decimal": 5000.0}
        assert self.engine._matches_filters(event, sub) is False


class TestBuildCursor:
    """Tests for _build_cursor — poll checkpoint tracking."""

    def test_empty_cursor_from_empty_old_cursor(self):
        """Empty old cursor + minimal event should still produce a cursor."""
        cursor = WebhookEngine._build_cursor(None, {"chain": "ethereum"})
        assert isinstance(cursor, dict)

    def test_cursor_from_block_timestamp(self):
        """Cursor should capture block_timestamp from last event."""
        cursor = WebhookEngine._build_cursor(
            None,
            {"block_timestamp": "2026-07-30T12:00:00", "chain": "ethereum"},
        )
        assert cursor.get("since") == "2026-07-30T12:00:00"

    def test_cursor_from_deployed_at(self):
        """Cursor should fall back to deployed_at."""
        cursor = WebhookEngine._build_cursor(
            None,
            {"deployed_at": "2026-07-29T12:00:00"},
        )
        assert cursor.get("since") == "2026-07-29T12:00:00"

    def test_cursor_preserves_old_cursor_fields(self):
        """Old cursor fields should be preserved."""
        cursor = WebhookEngine._build_cursor(
            {"existing_key": "existing_value"},
            {"block_timestamp": "2026-07-30T12:00:00"},
        )
        assert cursor.get("existing_key") == "existing_value"
        assert cursor.get("since") == "2026-07-30T12:00:00"

    def test_cursor_block_timestamp_preferred(self):
        """block_timestamp should be preferred over deployed_at."""
        cursor = WebhookEngine._build_cursor(
            None,
            {
                "block_timestamp": "2026-07-30T12:00:00",
                "deployed_at": "2026-07-29T12:00:00",
            },
        )
        assert cursor.get("since") == "2026-07-30T12:00:00"


class TestBuildSubscriptionMap:
    """Tests for _build_subscription_map — token address routing."""

    @pytest.mark.asyncio
    async def test_catch_all_without_token_filters(self):
        """Subscriptions without token filters should map to *."""
        engine = WebhookEngine()

        # Mock repo to return a subscription with no token filter
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
        )

        with patch.object(engine._repo, "list_by_event_type", AsyncMock(return_value=[sub])):
            sub_map = await engine._build_subscription_map(WebhookEventType.TOKEN_DEPLOYED)

        assert "*" in sub_map
        assert len(sub_map["*"]) == 1
        assert sub_map["*"][0].id == "whk:1"

    @pytest.mark.asyncio
    async def test_token_filter_routes_by_address(self):
        """Subscriptions with token filters should route by address."""
        engine = WebhookEngine()

        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(token_addresses=["0xABC", "0xDEF"]),
        )

        with patch.object(engine._repo, "list_by_event_type", AsyncMock(return_value=[sub])):
            sub_map = await engine._build_subscription_map(WebhookEventType.TOKEN_DEPLOYED)

        assert "0xabc" in sub_map
        assert "0xdef" in sub_map
        assert len(sub_map["0xabc"]) == 1

    @pytest.mark.asyncio
    async def test_mixed_filters(self):
        """Mix of token-filtered and catch-all subscriptions."""
        engine = WebhookEngine()

        sub1 = WebhookSubscription(
            id="whk:1",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
        )
        sub2 = WebhookSubscription(
            id="whk:2",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(token_addresses=["0xabc"]),
        )

        with patch.object(engine._repo, "list_by_event_type", AsyncMock(return_value=[sub1, sub2])):
            sub_map = await engine._build_subscription_map(WebhookEventType.TOKEN_DEPLOYED)

        assert "*" in sub_map
        assert "0xabc" in sub_map
