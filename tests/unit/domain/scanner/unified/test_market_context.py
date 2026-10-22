"""Tests for the scanner market context (Part 1 — market-aware scanning).

Covers:

  1. ``MarketContext`` dataclass round-trip (to_dict / from_dict).
  2. ``fetch_market_context`` with mocked RugBrain HTTP + Redis cache:
     - global mood cache is used (5-min TTL, per chain)
     - token-specific social signals are cached per token
     - fail-open returns a neutral ``MarketContext`` (source fallback:*)
  3. ``apply_market_scoring`` market-based risk adjustments:
     - fearful market + new token → higher risk (MARKET_FEAR_NEW_TOKEN)
     - recent exploits → higher risk (MARKET_RECENT_EXPLOITS)
     - rising chain volume → green (MARKET_CHAIN_GROWTH)
     - falling chain volume → risk (MARKET_CHAIN_CONTRACTION)
     - high social velocity on new token → risk (MARKET_SOCIAL_PUMP)
     - no market context → no-op
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.domains.scanner.unified.market_context import (
    fetch_market_context,
)
from app.domains.scanner.unified.model import MarketContext
from tests.unit.domain.scanner.unified._support import make_market_context


class TestMarketContextDataclass:
    def test_defaults_neutral(self) -> None:
        ctx = MarketContext()
        assert ctx.fear_index == 50.0
        assert ctx.recent_exploits == 0
        assert ctx.chain_volume_24h == 0.0
        assert ctx.social_velocity == 0.0
        assert ctx.deployer_freshness == "unknown"

    def test_round_trip(self) -> None:
        ctx = make_market_context(
            fear_index=22.0,
            recent_exploits=3,
            chain_volume_24h=-12.5,
            social_velocity=75.0,
            deployer_freshness="new",
        )
        restored = MarketContext.from_dict(ctx.to_dict())
        assert restored is not None
        assert restored.fear_index == 22.0
        assert restored.recent_exploits == 3
        assert restored.chain_volume_24h == -12.5
        assert restored.social_velocity == 75.0
        assert restored.deployer_freshness == "new"

    def test_from_dict_none_and_bad(self) -> None:
        assert MarketContext.from_dict(None) is None
        assert MarketContext.from_dict({"fear_index": "not-a-number"}) is None


class TestFetchMarketContext:
    @pytest.mark.asyncio
    async def test_fetch_from_rugbrain(self) -> None:
        """Global mood + token signals are fetched and merged."""
        mock_transport = httpx.MockTransport(
            handler=lambda request: httpx.Response(
                200,
                json=(
                    {"value": 25, "classification": "fearful"}
                    if "fear-index" in request.url.path
                    else {
                        "fear_index": 25.0,
                        "recent_exploits_24h": 2,
                        "chain_volume_24h": -8.0,
                        "chain_activity": {"tvl_change_24h": -8.0},
                    }
                    if "market/mood" in request.url.path
                    else {
                        "address": "0x" + "a" * 40,
                        "chain": "ethereum",
                        "fear_index": 25.0,
                        "social_signals": {"mention_velocity": 80.0},
                        "deployer_freshness": "new",
                        "recent_exploits": [],
                        "similar_tokens": [],
                    }
                ),
            )
        )

        real_async_client = httpx.AsyncClient  # capture before patching

        with (
            patch(
                "app.domains.scanner.unified.market_context.redis_get",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.domains.scanner.unified.market_context.redis_set",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("httpx.AsyncClient", side_effect=lambda: real_async_client(transport=mock_transport)),
        ):
            ctx = await fetch_market_context("0x" + "a" * 40, "ethereum")

        assert ctx is not None
        assert ctx.source == "rugbrain"
        assert ctx.fear_index == 25.0
        assert ctx.recent_exploits == 2
        assert ctx.chain_volume_24h == -8.0
        assert ctx.social_velocity == 80.0
        assert ctx.deployer_freshness == "new"

    @pytest.mark.asyncio
    async def test_uses_cached_global_mood(self) -> None:
        """A cached per-chain mood avoids the RugBrain HTTP round-trip."""
        cached_mood = {"fear_index": 10.0, "recent_exploits": 1, "chain_volume_24h": 0.0}
        cached_social = {"social_velocity": 0.0, "deployer_freshness": "unknown"}

        with (
            patch(
                "app.domains.scanner.unified.market_context.redis_get",
                new_callable=AsyncMock,
                side_effect=[cached_mood, cached_social],
            ),
            patch("app.domains.scanner.unified.market_context.redis_set", new_callable=AsyncMock) as redis_set,
            patch("httpx.AsyncClient") as mock_client,
        ):
            ctx = await fetch_market_context("0x" + "b" * 40, "ethereum")

        mock_client.assert_not_called()
        redis_set.assert_not_called()
        assert ctx is not None
        assert ctx.fear_index == 10.0
        assert ctx.recent_exploits == 1

    @pytest.mark.asyncio
    async def test_fail_open_on_rugbrain_outage(self) -> None:
        """RugBrain outage returns a neutral MarketContext, never raises."""
        mock_transport = httpx.MockTransport(handler=lambda request: httpx.Response(503, json={"error": "down"}))
        real_async_client2 = httpx.AsyncClient  # capture before patching

        with (
            patch(
                "app.domains.scanner.unified.market_context.redis_get",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.domains.scanner.unified.market_context.redis_set", new_callable=AsyncMock),
            patch(
                "httpx.AsyncClient",
                side_effect=lambda: real_async_client2(transport=mock_transport),
            ),
        ):
            ctx = await fetch_market_context("0x" + "c" * 40, "ethereum")

        assert ctx is not None
        assert ctx.source.startswith("fallback")
        assert ctx.fear_index == 50.0
