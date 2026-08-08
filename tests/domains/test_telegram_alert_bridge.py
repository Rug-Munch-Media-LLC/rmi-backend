"""Tests for the intelligent webhook → Telegram alert bridge.

Covers the two halves of the bridge:

Backend (app.domains.scanner.webhooks.telegram_bridge):
  - internal subscription creation / removal
  - enqueue_telegram_alert → Redis LIST ``bot:alerts:queue``
  - route_internal_telegram: internal subs enqueue (no HTTP), external skip

Bot (app.domains.telegram.rugmunchbot.alert_worker):
  - HTML formatting per event type (whale / price / liquidity / launch)
  - deliver_pending_alerts pops the queue and sends via ctx.bot

Redis is a minimal in-memory async stand-in (same pattern as the scanner
stability tests); get_redis_async is patched at the consumer namespaces.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domains.scanner.webhooks.models import (
    WebhookEvent,
    WebhookEventType,
    WebhookSubscription,
)
from app.domains.scanner.webhooks.telegram_bridge import (
    INTERNAL_TELEGRAM_PREFIX,
    TELEGRAM_ALERT_QUEUE_KEY,
    enqueue_telegram_alert,
    is_telegram_subscription,
    route_internal_telegram,
    subscribe_telegram_user,
    telegram_user_id,
    unsubscribe_telegram_user,
)
from app.domains.telegram.rugmunchbot import alert_worker

# ── FakeRedis stand-in ─────────────────────────────────────────────────


class FakeRedis:
    """Minimal in-memory async Redis stand-in (list + hash + get/set)."""

    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._data: dict[str, str] = {}

    # LIST ops
    async def lpush(self, key: str, value: str) -> int:
        self._lists.setdefault(key, []).append(value)
        return 1

    async def rpop(self, key: str) -> str | None:
        lst = self._lists.get(key)
        if not lst:
            return None
        return lst.pop(0)

    def list_items(self, key: str) -> list[str]:
        return list(self._lists.get(key, []))

    # HASH ops
    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    async def hget(self, key: str, field: str) -> str | None:
        return self._hashes.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: str) -> int:
        self._hashes.setdefault(key, {})[field] = value
        return 1

    async def hdel(self, key: str, *fields: str) -> int:
        h = self._hashes.get(key, {})
        removed = sum(1 for f in fields if h.pop(f, None) is not None)
        return removed

    # STRING ops
    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str) -> None:
        self._data[key] = value


def _patch_redis(fake: FakeRedis) -> list[Mock]:
    """Patch get_redis_async in every consumer namespace that binds at import."""
    patchers = [
        patch("app.domains.scanner.webhooks.telegram_bridge.get_redis_async", new=Mock(return_value=fake)),
        patch("app.domains.scanner.webhooks.repository.get_redis_async", new=Mock(return_value=fake)),
        patch("app.domains.telegram.rugmunchbot.alert_worker.get_redis_async", new=Mock(return_value=fake)),
    ]
    for p in patchers:
        p.start()
    return patchers


@pytest.fixture
def fake_redis() -> tuple[FakeRedis, list[Mock]]:
    fake = FakeRedis()
    patchers = _patch_redis(fake)
    yield fake, patchers
    for p in patchers:
        p.stop()


# ── Backend bridge ─────────────────────────────────────────────────────


class TestTelegramSubscription:
    async def test_subscribe_creates_internal_sub(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        fake, _ = fake_redis
        sub = await subscribe_telegram_user(
            123456,
            [WebhookEventType.TOKEN_WHALE_MOVE, WebhookEventType.TOKEN_PRICE_CHANGE],
            token_addresses=["0xABC"],
            chains=["ethereum"],
        )
        assert sub is not None
        assert sub.url == f"{INTERNAL_TELEGRAM_PREFIX}123456"
        assert is_telegram_subscription(sub)
        assert telegram_user_id(sub) == 123456
        assert set(sub.event_types) == {
            WebhookEventType.TOKEN_WHALE_MOVE,
            WebhookEventType.TOKEN_PRICE_CHANGE,
        }
        assert sub.filters.token_addresses == ["0xabc"]
        assert sub.filters.chains == ["ethereum"]
        assert sub.owner_id == "123456"
        # Persisted in the webhook repo hash
        stored = fake._hashes.get("rmi:webhooks", {})
        assert sub.id in stored
        assert json.loads(stored[sub.id])["url"].startswith(INTERNAL_TELEGRAM_PREFIX)

    async def test_subscribe_empty_event_types_returns_none(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        assert await subscribe_telegram_user(1, []) is None

    async def test_unsubscribe_removes_only_matching(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        fake, _ = fake_redis
        await subscribe_telegram_user(42, [WebhookEventType.TOKEN_WHALE_MOVE], token_addresses=["0xAAA"])
        await subscribe_telegram_user(42, [WebhookEventType.TOKEN_WHALE_MOVE], token_addresses=["0xBBB"])
        await subscribe_telegram_user(99, [WebhookEventType.TOKEN_WHALE_MOVE], token_addresses=["0xAAA"])

        removed = await unsubscribe_telegram_user(
            42,
            event_types=[WebhookEventType.TOKEN_WHALE_MOVE],
            token_addresses=["0xAAA"],
        )
        assert removed == 1

        # The 0xAAA sub for user 42 is gone; 0xBBB (42) and 0xAAA (99) remain
        remaining = fake._hashes.get("rmi:webhooks", {})
        assert len(remaining) == 2
        kept_urls = sorted(json.loads(v)["url"] for v in remaining.values())
        assert kept_urls == [f"{INTERNAL_TELEGRAM_PREFIX}42", f"{INTERNAL_TELEGRAM_PREFIX}99"]

    async def test_unsubscribe_by_type_only(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        await subscribe_telegram_user(7, [WebhookEventType.TOKEN_PRICE_CHANGE], token_addresses=["0xCCC"])
        await subscribe_telegram_user(7, [WebhookEventType.TOKEN_WHALE_MOVE], token_addresses=["0xCCC"])
        removed = await unsubscribe_telegram_user(
            7,
            event_types=[WebhookEventType.TOKEN_PRICE_CHANGE],
        )
        assert removed == 1


class TestEnqueueAndRoute:
    async def test_enqueue_telegram_alert_pushes_json(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        fake, _ = fake_redis
        ok = await enqueue_telegram_alert(123, "token.whale_move", {"value_decimal": 50000}, "evt:1")
        assert ok
        items = fake.list_items(TELEGRAM_ALERT_QUEUE_KEY)
        assert len(items) == 1
        parsed = json.loads(items[0])
        assert parsed["type"] == "token.whale_move"
        assert parsed["user_id"] == 123
        assert parsed["event_id"] == "evt:1"
        assert parsed["payload"]["value_decimal"] == 50000

    async def test_enqueue_redis_unavailable_fails_open(self) -> None:
        with patch("app.domains.scanner.webhooks.telegram_bridge.get_redis_async", new=Mock(return_value=None)):
            assert await enqueue_telegram_alert(1, "token.whale_move", {}, "evt:1") is False

    async def test_route_internal_telegram_enqueues(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        fake, _ = fake_redis
        sub = WebhookSubscription.model_construct(
            id="whk:tg:9:1",
            url=f"{INTERNAL_TELEGRAM_PREFIX}9",
            event_types=[WebhookEventType.TOKEN_WHALE_MOVE],
            filters=None,
            active=True,
        )
        event = WebhookEvent(
            id="evt:r1", event_type=WebhookEventType.TOKEN_WHALE_MOVE, payload={"value_decimal": 999}, chain="solana"
        )
        handled = await route_internal_telegram(event, sub)
        assert handled is True
        items = fake.list_items(TELEGRAM_ALERT_QUEUE_KEY)
        assert len(items) == 1
        parsed = json.loads(items[0])
        assert parsed["user_id"] == 9
        assert parsed["payload"]["chain"] == "solana"

    async def test_route_external_returns_false(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        fake, _ = fake_redis
        sub = WebhookSubscription.model_construct(
            id="whk:1:1",
            url="https://example.com/hook",
            event_types=[WebhookEventType.TOKEN_WHALE_MOVE],
            filters=None,
            active=True,
        )
        event = WebhookEvent(id="evt:x1", event_type=WebhookEventType.TOKEN_WHALE_MOVE, payload={}, chain="eth")
        assert await route_internal_telegram(event, sub) is False
        assert fake.list_items(TELEGRAM_ALERT_QUEUE_KEY) == []


# ── Bot delivery worker ────────────────────────────────────────────────


class TestFormatAlert:
    def _item(self, etype: str, payload: dict, user_id: int = 11) -> dict:
        return {
            "type": etype,
            "user_id": user_id,
            "event_id": f"evt-{etype}",
            "payload": payload,
            "created_at": "2026-08-08T00:00:00+00:00",
        }

    def test_whale_move_format_has_scan_link(self) -> None:
        text = alert_worker._format_alert(
            self._item(
                "token.whale_move",
                {
                    "token_address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                    "token_symbol": "PEPE",
                    "value_decimal": 125000.0,
                    "from_address": "0x1111111111111111111111111111111111111111",
                    "to_address": "0x2222222222222222222222222222222222222222",
                    "chain": "ethereum",
                },
            )
        )
        assert "🐋" in text
        assert "Whale Move" in text
        assert "PEPE" in text
        assert "/scan" in text
        assert "0x6982" in text
        assert "$125.0K" in text
        assert "ETH" in text

    def test_price_change_format_direction_up(self) -> None:
        text = alert_worker._format_alert(
            self._item(
                "token.price_change",
                {
                    "token_address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "token_symbol": "USDC",
                    "change_pct": 5.2,
                    "direction": "up",
                    "price": 1.001,
                    "chain": "solana",
                },
            )
        )
        assert "📈" in text
        assert "5.2%" in text
        assert "SOL" in text
        assert "/scan" in text

    def test_liquidity_change_format(self) -> None:
        text = alert_worker._format_alert(
            self._item(
                "token.liquidity_change",
                {
                    "token_address": "0xabc",
                    "token_symbol": "LIQ",
                    "value_decimal": 6500.0,
                    "chain": "bsc",
                },
            )
        )
        assert "💧" in text
        assert "LIQ" in text
        assert "BSC" in text

    def test_token_deployed_format(self) -> None:
        text = alert_worker._format_alert(
            self._item(
                "token.deployed",
                {
                    "token_address": "0xdef",
                    "token_symbol": "NEW",
                    "token_name": "NewToken",
                    "deployer_address": "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                    "chain": "polygon",
                },
            )
        )
        assert "🆕" in text
        assert "NewToken" in text
        assert "NEW" in text
        assert "MATIC" in text

    def test_first_swap_format(self) -> None:
        text = alert_worker._format_alert(
            self._item(
                "token.first_swap",
                {"token_address": "0x1", "token_symbol": "SWP", "value_decimal": 100.0, "chain": "base"},
            )
        )
        assert "🔄" in text
        assert "BASE" in text

    def test_wallet_activity_format(self) -> None:
        text = alert_worker._format_alert(
            self._item(
                "wallet.activity",
                {
                    "token_address": "0x2",
                    "from_address": "0xaaa",
                    "to_address": "0xbbb",
                    "value_decimal": 250.0,
                    "chain": "ethereum",
                },
            )
        )
        assert "👛" in text
        assert "/scan" in text

    def test_unknown_type_fallback(self) -> None:
        text = alert_worker._format_alert(self._item("chain.block", {"chain": "ethereum"}, user_id=1))
        assert "🔔" in text
        assert "Alert" in text


class TestDeliverPendingAlerts:
    async def test_delivers_queued_alerts(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        fake, _ = fake_redis
        await enqueue_telegram_alert(
            555,
            "token.whale_move",
            {"token_address": "0xabc", "token_symbol": "WAL", "value_decimal": 90000, "chain": "eth"},
            "evt:d1",
        )

        bot = AsyncMock()
        ctx = Mock()
        ctx.bot = bot

        processed = await alert_worker.deliver_pending_alerts(ctx)
        assert processed == 1
        bot.send_message.assert_awaited_once()
        args = bot.send_message.await_args
        assert args.kwargs["chat_id"] == 555
        assert args.kwargs["parse_mode"] == "HTML"
        assert "🐋" in args.kwargs["text"]
        assert fake.list_items(TELEGRAM_ALERT_QUEUE_KEY) == []

    async def test_empty_queue_returns_zero(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        bot = AsyncMock()
        ctx = Mock()
        ctx.bot = bot
        assert await alert_worker.deliver_pending_alerts(ctx) == 0
        bot.send_message.assert_not_awaited()

    async def test_dedupe_same_event_id(self, fake_redis: tuple[FakeRedis, list[Mock]]) -> None:
        fake, _ = fake_redis
        alert_worker._RECENT_EVENT_IDS.clear()
        await enqueue_telegram_alert(
            1, "token.price_change", {"token_address": "0x1", "change_pct": 6, "chain": "eth"}, "evt:dup"
        )
        await enqueue_telegram_alert(
            2, "token.price_change", {"token_address": "0x2", "change_pct": 7, "chain": "eth"}, "evt:dup"
        )
        bot = AsyncMock()
        ctx = Mock()
        ctx.bot = bot
        processed = await alert_worker.deliver_pending_alerts(ctx)
        # Second copy is deduped → only one send
        assert processed == 2
        assert bot.send_message.await_count == 1
        alert_worker._RECENT_EVENT_IDS.clear()
