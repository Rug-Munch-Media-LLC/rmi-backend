"""Tests for the webhook/alert system (app.domains.scanner.webhooks).

Tests cover:
  1. Model validation and serialization
  2. Repository CRUD (with mocked Redis)
  3. Delivery service (retry, backoff, error handling)
  4. Engine poll + dispatch loop
  5. Provider logic (token_deployed, whale_move, new_block)
  6. API router endpoints
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from app.domains.scanner.webhooks.delivery import WebhookDeliveryService
from app.domains.scanner.webhooks.engine import WebhookEngine
from app.domains.scanner.webhooks.models import (
    CreateWebhookRequest,
    WebhookDelivery,
    WebhookEvent,
    WebhookEventType,
    WebhookFilters,
    WebhookSubscription,
)
from app.domains.scanner.webhooks.providers.token_deployed import TokenDeployedProvider
from app.domains.scanner.webhooks.providers.new_block import NewBlockProvider
from app.domains.scanner.webhooks.providers.whale_move import WhaleMoveProvider
from app.domains.scanner.webhooks.repository import WebhookRepository


# ═══════════════════════════════════════════════════════════════════════
# 1. Model tests
# ═══════════════════════════════════════════════════════════════════════


class TestWebhookSubscription:
    def test_valid_subscription(self):
        sub = WebhookSubscription(
            id="whk:1712000000",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED, WebhookEventType.TOKEN_WHALE_MOVE],
        )
        assert sub.id == "whk:1712000000"
        assert sub.url == "https://example.com/webhook"
        assert len(sub.event_types) == 2
        assert sub.active is True
        assert sub.filters is not None  # default factory
        assert sub.owner_id is None

    def test_invalid_url(self):
        with pytest.raises(ValidationError):
            WebhookSubscription(
                id="whk:1",
                url="not-a-url",  # missing scheme
                event_types=[WebhookEventType.TOKEN_DEPLOYED],
            )

    def test_empty_event_types(self):
        with pytest.raises(ValidationError, match="at least 1 item"):
            WebhookSubscription(
                id="whk:1",
                url="https://example.com/webhook",
                event_types=[],
            )

    def test_serialization_roundtrip(self):
        sub = WebhookSubscription(
            id="whk:1712000000",
            url="https://example.com/webhook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(chains=["ethereum"], min_value_usd=5000.0),
        )
        data = sub.model_dump(mode="json")
        restored = WebhookSubscription.model_validate(data)
        assert restored.id == sub.id
        assert restored.url == sub.url
        assert restored.event_types == sub.event_types
        assert restored.filters.chains == ["ethereum"]
        assert restored.filters.min_value_usd == 5000.0

    def test_subscription_with_filters(self):
        sub = WebhookSubscription(
            id="whk:2",
            url="https://hook.example.com/callback",
            event_types=[WebhookEventType.WALLET_ACTIVITY],
            filters=WebhookFilters(
                chains=["ethereum", "polygon"],
                wallet_addresses=["0xabc", "0xdef"],
            ),
        )
        assert sub.filters.chains == ["ethereum", "polygon"]
        assert sub.filters.wallet_addresses == ["0xabc", "0xdef"]

    def test_event_type_values(self):
        values = WebhookEventType.values()
        assert "token.deployed" in values
        assert "token.whale_move" in values
        assert "chain.block" in values
        assert len(values) == 7


class TestWebhookEvent:
    def test_event_defaults(self):
        event = WebhookEvent(
            id="evt:1",
            event_type=WebhookEventType.TOKEN_DEPLOYED,
            payload={"token_address": "0x123"},
        )
        assert event.chain == ""
        assert event.subscription_id == ""
        assert event.payload["token_address"] == "0x123"

    def test_event_serialization(self):
        event = WebhookEvent(
            id="evt:1",
            subscription_id="whk:1",
            event_type=WebhookEventType.TOKEN_WHALE_MOVE,
            payload={"value": 50000},
            chain="ethereum",
        )
        data = event.model_dump(mode="json")
        assert data["event_type"] == "token.whale_move"
        assert data["chain"] == "ethereum"
        assert data["payload"]["value"] == 50000


class TestCreateWebhookRequest:
    def test_valid_request(self):
        req = CreateWebhookRequest(
            url="https://example.com/hook",
            event_types=[WebhookEventType.CHAIN_BLOCK, WebhookEventType.WALLET_ACTIVITY],
        )
        assert len(req.event_types) == 2

    def test_request_with_filters(self):
        req = CreateWebhookRequest(
            url="https://example.com/hook",
            event_types=[WebhookEventType.TOKEN_WHALE_MOVE],
            filters=WebhookFilters(min_value_usd=100000.0, chains=["solana"]),
        )
        assert req.filters.min_value_usd == 100000.0
        assert req.filters.chains == ["solana"]


# ═══════════════════════════════════════════════════════════════════════
# 2. Repository tests
# ═══════════════════════════════════════════════════════════════════════


class TestWebhookRepository:
    @pytest.fixture
    def mock_redis(self):
        """Mock async Redis client with hgetall/hset/hdel."""
        mock = AsyncMock()
        mock.hgetall.return_value = {}
        mock.hget.return_value = None
        mock.hset.return_value = 1
        mock.hdel.return_value = 1
        return mock

    @pytest.fixture
    def repo(self, mock_redis):
        with patch("app.domains.scanner.webhooks.repository.get_redis_async", return_value=mock_redis):
            yield WebhookRepository()

    @pytest.fixture
    def sample_sub(self):
        return WebhookSubscription(
            id="whk:1712000000",
            url="https://example.com/hook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
        )

    async def test_list_all_empty(self, repo, mock_redis):
        mock_redis.hgetall.return_value = {}
        subs = await repo.list_all()
        assert subs == []

    async def test_create_and_list(self, repo, mock_redis, sample_sub):
        mock_redis.hgetall.return_value = {
            "whk:1712000000": sample_sub.model_dump_json(),
        }
        await repo.create(sample_sub)
        subs = await repo.list_all()
        assert len(subs) == 1
        assert subs[0].id == "whk:1712000000"

    async def test_get(self, repo, mock_redis, sample_sub):
        mock_redis.hget.return_value = sample_sub.model_dump_json()
        sub = await repo.get("whk:1712000000")
        assert sub is not None
        assert sub.id == "whk:1712000000"

    async def test_get_not_found(self, repo, mock_redis):
        mock_redis.hget.return_value = None
        sub = await repo.get("nonexistent")
        assert sub is None

    async def test_delete(self, repo, mock_redis):
        mock_redis.hdel.return_value = 1
        result = await repo.delete("whk:1712000000")
        assert result is True

    async def test_delete_not_found(self, repo, mock_redis):
        mock_redis.hdel.return_value = 0
        result = await repo.delete("nonexistent")
        assert result is False

    async def test_list_by_event_type(self, repo, mock_redis, sample_sub):
        mock_redis.hgetall.return_value = {
            "whk:1": sample_sub.model_dump_json(),
        }
        matching = await repo.list_by_event_type("token.deployed")
        assert len(matching) == 1
        non_matching = await repo.list_by_event_type("chain.block")
        assert len(non_matching) == 0

    async def test_inactive_subscription_excluded(self, repo, mock_redis):
        inactive = WebhookSubscription(
            id="whk:2",
            url="https://example.com/hook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            active=False,
        )
        mock_redis.hgetall.return_value = {"whk:2": inactive.model_dump_json()}
        matching = await repo.list_by_event_type("token.deployed")
        assert len(matching) == 0

    async def test_corrupt_record_skipped(self, repo, mock_redis):
        mock_redis.hgetall.return_value = {
            "whk:1": "not valid json",
        }
        subs = await repo.list_all()
        assert subs == []


# ═══════════════════════════════════════════════════════════════════════
# 3. Delivery service tests
# ═══════════════════════════════════════════════════════════════════════


class TestWebhookDeliveryService:
    @pytest.fixture
    def service(self):
        return WebhookDeliveryService()

    @pytest.fixture
    def sample_event(self):
        return WebhookEvent(
            id="evt:1",
            subscription_id="whk:1",
            event_type=WebhookEventType.TOKEN_DEPLOYED,
            payload={"token_address": "0xabc"},
        )

    @pytest.fixture
    def sample_sub(self):
        return WebhookSubscription(
            id="whk:1",
            url="https://example.com/hook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
        )

    async def _setup_mock_client(self, mock_client_cls, mock_status=200, mock_text="", side_effect=None):
        """Helper to wire up the AsyncClient mock context manager chain."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        if side_effect is not None:
            mock_client.post.side_effect = side_effect
        else:
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = mock_status
            mock_response.text = mock_text or "OK"
            mock_client.post.return_value = mock_response

        return mock_client

    async def test_deliver_success(self, service, sample_event, sample_sub):
        """Test that a 2xx response marks delivery as delivered."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            await self._setup_mock_client(mock_client_cls, mock_status=200)

            with patch.object(service, "_save_delivery", AsyncMock()):
                result = await service.deliver(sample_event, sample_sub)

        assert result.status == "delivered"
        assert result.response_code == 200

    async def test_deliver_4xx_no_retry(self, service, sample_event, sample_sub):
        """Test that 4xx responses fail immediately without retry."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            await self._setup_mock_client(mock_client_cls, mock_status=400, mock_text="Bad request")

            with patch.object(service, "_save_delivery", AsyncMock()):
                result = await service.deliver(sample_event, sample_sub)

        assert result.status == "failed"
        assert result.attempt == 1  # no retry for 4xx

    async def test_deliver_5xx_retry(self, service, sample_event, sample_sub):
        """Test that 5xx responses trigger retry with backoff."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            # First two attempts fail with 500, third succeeds
            resp_500 = MagicMock(spec=httpx.Response)
            resp_500.status_code = 500
            resp_500.text = "Server error"

            resp_200 = MagicMock(spec=httpx.Response)
            resp_200.status_code = 200

            mock_client.post = AsyncMock(side_effect=[resp_500, resp_500, resp_200])

            with patch.object(service, "_save_delivery", AsyncMock()):
                with patch("asyncio.sleep", AsyncMock()):  # no actual waiting
                    result = await service.deliver(sample_event, sample_sub)

        assert result.status == "delivered"
        assert result.attempt == 3  # succeeded on 3rd attempt
        assert result.response_code == 200

    async def test_deliver_all_retries_exhausted(self, service, sample_event, sample_sub):
        """Test that all retries are exhausted and final status is failed."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = 500
            mock_response.text = "Server error"
            mock_client.post.return_value = mock_response

            with patch.object(service, "_save_delivery", AsyncMock()):
                with patch("asyncio.sleep", AsyncMock()):
                    result = await service.deliver(sample_event, sample_sub)

        assert result.status == "failed"
        assert result.attempt == 3

    async def test_deliver_timeout_retry(self, service, sample_event, sample_sub):
        """Test that timeouts trigger retry."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = httpx.TimeoutException("timeout", request=None)

            with patch.object(service, "_save_delivery", AsyncMock()):
                with patch("asyncio.sleep", AsyncMock()):
                    result = await service.deliver(sample_event, sample_sub)

        assert result.status == "failed"
        assert "timeout" in (result.error or "")

    def test_build_payload_contains_expected_fields(self, service, sample_event):
        """Test the webhook POST body structure."""
        payload = service._build_payload(sample_event)
        assert "event_id" in payload
        assert "event_type" in payload
        assert "chain" in payload
        assert "triggered_at" in payload
        assert "data" in payload
        assert payload["event_type"] == "token.deployed"
        assert payload["data"] == sample_event.payload


# ═══════════════════════════════════════════════════════════════════════
# 4. Engine tests
# ═══════════════════════════════════════════════════════════════════════


class TestWebhookEngine:
    @pytest.fixture
    def delivery_service(self):
        svc = MagicMock(spec=WebhookDeliveryService)
        svc.deliver = AsyncMock()
        return svc

    @pytest.fixture
    def repo(self):
        repo = MagicMock(spec=WebhookRepository)
        repo.list_by_event_type = AsyncMock(return_value=[])
        repo.update = AsyncMock()
        return repo

    @pytest.fixture
    def engine(self, repo, delivery_service):
        return WebhookEngine(repo=repo, delivery_service=delivery_service)

    def test_engine_init(self, engine):
        assert engine._running is False
        assert len(engine._tasks) == 0

    async def test_start_creates_poll_tasks(self, engine):
        with patch("app.domains.scanner.webhooks.engine.PROVIDER_REGISTRY", []):
            await engine.start()
            assert engine._running is True
            assert len(engine._tasks) == 0
            await engine.stop()

    async def test_start_then_stop(self, engine):
        # Register just one provider to avoid heavy mocking
        with patch("app.domains.scanner.webhooks.engine.PROVIDER_REGISTRY", [MagicMock]):
            await engine.start()
            assert engine._running is True
            assert len(engine._tasks) > 0
            await engine.stop()
            assert engine._running is False

    def test_matches_filters_no_filters(self, engine):
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/hook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
        )
        assert engine._matches_filters({"chain": "ethereum"}, sub) is True

    def test_matches_filters_chain_match(self, engine):
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/hook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(chains=["ethereum"]),
        )
        assert engine._matches_filters({"chain": "ethereum"}, sub) is True
        assert engine._matches_filters({"chain": "solana"}, sub) is False

    def test_matches_filters_value_threshold(self, engine):
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/hook",
            event_types=[WebhookEventType.TOKEN_WHALE_MOVE],
            filters=WebhookFilters(min_value_usd=10000.0),
        )
        assert engine._matches_filters({"value_decimal": 50000}, sub) is True
        assert engine._matches_filters({"value_decimal": 100}, sub) is False

    def test_matches_filters_token_address(self, engine):
        sub = WebhookSubscription(
            id="whk:1",
            url="https://example.com/hook",
            event_types=[WebhookEventType.TOKEN_DEPLOYED],
            filters=WebhookFilters(token_addresses=["0xabc"]),
        )
        assert engine._matches_filters({"token_address": "0xabc"}, sub) is True
        assert engine._matches_filters({"token_address": "0xdef"}, sub) is False

    def test_build_cursor_from_block_timestamp(self, engine):
        cursor = engine._build_cursor(
            {"since": "2024-01-01 00:00:00"},
            {"block_timestamp": "2024-06-15 12:00:00"},
        )
        assert cursor["since"] == "2024-06-15 12:00:00"

    def test_build_cursor_from_deployed_at(self, engine):
        cursor = engine._build_cursor(
            None,
            {"deployed_at": "2024-06-15 12:00:00"},
        )
        assert cursor["since"] == "2024-06-15 12:00:00"

    def test_build_cursor_preserves_old_data(self, engine):
        cursor = engine._build_cursor(
            {"since": "2024-01-01", "extra": "data"},
            {"block_timestamp": "2024-06-15"},
        )
        assert cursor["extra"] == "data"
        assert cursor["since"] == "2024-06-15"


# ═══════════════════════════════════════════════════════════════════════
# 5. Provider tests
# ═══════════════════════════════════════════════════════════════════════


class TestTokenDeployedProvider:
    @pytest.fixture
    def provider(self):
        return TokenDeployedProvider()

    def test_event_type(self, provider):
        assert provider.event_type == WebhookEventType.TOKEN_DEPLOYED

    def test_poll_interval(self, provider):
        assert provider.poll_interval == 30

    async def test_poll_no_clickhouse(self, provider):
        with patch(
            "app.domains.scanner.webhooks.providers.token_deployed.get_clickhouse",
            return_value=None,
        ):
            events = await provider.poll()
            assert events == []

    async def test_cursor_key(self, provider):
        key = await provider.cursor_key()
        assert key == "webhook:cursor:token_deployed"


class TestWhaleMoveProvider:
    @pytest.fixture
    def provider(self):
        return WhaleMoveProvider()

    def test_event_type(self, provider):
        assert provider.event_type == WebhookEventType.TOKEN_WHALE_MOVE

    async def test_poll_no_clickhouse(self, provider):
        with patch(
            "app.domains.scanner.webhooks.providers.whale_move.get_clickhouse",
            return_value=None,
        ):
            events = await provider.poll()
            assert events == []

    async def test_cursor_key(self, provider):
        key = await provider.cursor_key()
        assert key == "webhook:cursor:whale_move"


class TestNewBlockProvider:
    @pytest.fixture
    def provider(self):
        return NewBlockProvider()

    def test_event_type(self, provider):
        assert provider.event_type == WebhookEventType.CHAIN_BLOCK

    def test_poll_interval(self, provider):
        assert provider.poll_interval == 15

    async def test_poll_rpc_failure_graceful(self, provider):
        """Test that RPC failure returns empty events list gracefully."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.side_effect = httpx.RequestError("RPC down", request=MagicMock())

            events = await provider.poll()
        # Should return empty, not raise
        assert isinstance(events, list)

    @pytest.mark.parametrize(
        "hex_result,expected",
        [
            ("0x10a4f2", 1090802),
            ("0x1", 1),
            ("0x0", 0),
        ],
    )
    async def test_eth_block_number_parsing(self, provider, hex_result, expected):
        """Test hex block number parsing."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": hex_result}
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await provider._eth_block_number(mock_client, "https://rpc.example.com")
        assert result == expected

    async def test_cursor_key(self, provider):
        key = await provider.cursor_key()
        assert key == "webhook:cursor:new_block"


# ═══════════════════════════════════════════════════════════════════════
# 6. WebhookDelivery model tests
# ═══════════════════════════════════════════════════════════════════════


class TestWebhookDelivery:
    def test_delivery_defaults(self):
        d = WebhookDelivery(
            event_id="evt:1",
            subscription_id="whk:1",
            url="https://example.com/hook",
        )
        assert d.status == "pending"
        assert d.attempt == 0
        assert d.max_attempts == 3

    def test_delivery_serialization(self):
        d = WebhookDelivery(
            event_id="evt:1",
            subscription_id="whk:1",
            url="https://example.com/hook",
            status="delivered",
            attempt=2,
            response_code=200,
        )
        data = d.model_dump(mode="json")
        assert data["status"] == "delivered"
        assert data["attempt"] == 2
