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
from app.domains.scanner.unified.model import MarketContext, ScanResult
from app.domains.scanner.unified.scoring import (
    apply_market_scoring,
    compute_score,
)


def _ctx(**overrides: object) -> MarketContext:
    defaults: dict[str, object] = {
        "fear_index": 50.0,
        "recent_exploits": 0,
        "chain_volume_24h": 0.0,
        "social_velocity": 0.0,
        "deployer_freshness": "unknown",
        "source": "rugbrain",
    }
    defaults.update(overrides)
    return MarketContext(**defaults)  # type: ignore[arg-type]


class TestMarketContextDataclass:
    def test_defaults_neutral(self) -> None:
        ctx = MarketContext()
        assert ctx.fear_index == 50.0
        assert ctx.recent_exploits == 0
        assert ctx.chain_volume_24h == 0.0
        assert ctx.social_velocity == 0.0
        assert ctx.deployer_freshness == "unknown"

    def test_round_trip(self) -> None:
        ctx = _ctx(
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


class TestRugBrainAuthHeader:
    """Every RugBrain market call must carry the X-API-Key header."""

    @staticmethod
    def _recording_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if "fear-index" in request.url.path:
                return httpx.Response(200, json={"value": 30.0})
            return httpx.Response(200, json={"recent_exploits_24h": 0})

        return httpx.MockTransport(handler=handler)

    @pytest.mark.asyncio
    async def test_api_key_header_sent_on_every_call(self) -> None:
        seen: list[httpx.Request] = []
        transport = self._recording_transport(seen)
        real_client = httpx.AsyncClient

        with (
            patch(
                "app.domains.scanner.unified.market_context.redis_get",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.domains.scanner.unified.market_context.redis_set", new_callable=AsyncMock),
            patch("httpx.AsyncClient", side_effect=lambda: real_client(transport=transport)),
            patch("app.config.settings") as mock_settings,
        ):
            mock_settings.rmi_rag_api_key = "test-rag-key"
            await fetch_market_context("0x" + "d" * 40, "ethereum")

        assert len(seen) == 3  # fear-index, market/mood, market/context/{address}
        assert {r.headers.get("x-api-key") for r in seen} == {"test-rag-key"}

    @pytest.mark.asyncio
    async def test_missing_key_logs_once_and_does_not_crash(self) -> None:
        seen: list[httpx.Request] = []
        transport = self._recording_transport(seen)
        real_client = httpx.AsyncClient

        with (
            patch(
                "app.domains.scanner.unified.market_context.redis_get",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.domains.scanner.unified.market_context.redis_set", new_callable=AsyncMock),
            patch("httpx.AsyncClient", side_effect=lambda: real_client(transport=transport)),
            patch("app.config.settings") as mock_settings,
            patch("app.domains.scanner.unified.market_context.logger") as mock_logger,
            patch("app.domains.scanner.unified.market_context._missing_key_logged", False),
        ):
            mock_settings.rmi_rag_api_key = ""
            ctx = await fetch_market_context("0x" + "e" * 40, "ethereum")

        assert ctx is not None
        assert "x-api-key" not in seen[0].headers
        warnings = [c for c in mock_logger.warning.call_args_list if c.args[0] == "market_context_missing_api_key"]
        assert len(warnings) == 1


class TestApplyMarketScoring:
    def _scored_result(self, **mc_overrides: object) -> ScanResult:
        result = ScanResult(token_address="0xTEST", chain="solana", age_hours=12)
        result.market_context = _ctx(**mc_overrides)
        compute_score(result)
        return result

    def test_fearful_market_new_token_raises_risk(self) -> None:
        result = self._scored_result(fear_index=20.0, deployer_freshness="new")
        before = result.safety_score
        apply_market_scoring(result)
        assert result.safety_score < before
        assert "MARKET_FEAR_NEW_TOKEN" in result.risk_flags

    def test_recent_exploits_raise_risk(self) -> None:
        result = self._scored_result(recent_exploits=2)
        before = result.safety_score
        apply_market_scoring(result)
        assert result.safety_score < before
        assert "MARKET_RECENT_EXPLOITS" in result.risk_flags

    def test_rising_chain_volume_green(self) -> None:
        result = self._scored_result(chain_volume_24h=12.0)
        before = result.safety_score
        apply_market_scoring(result)
        assert result.safety_score > before
        assert "MARKET_CHAIN_GROWTH" in result.green_flags

    def test_falling_chain_volume_risk(self) -> None:
        result = self._scored_result(chain_volume_24h=-15.0)
        before = result.safety_score
        apply_market_scoring(result)
        assert result.safety_score < before
        assert "MARKET_CHAIN_CONTRACTION" in result.risk_flags

    def test_social_pump_on_new_token(self) -> None:
        result = self._scored_result(social_velocity=80.0, deployer_freshness="new")
        before = result.safety_score
        apply_market_scoring(result)
        assert result.safety_score < before
        assert "MARKET_SOCIAL_PUMP" in result.risk_flags

    def test_no_market_context_is_noop(self) -> None:
        result = ScanResult(token_address="0xTEST", chain="solana")
        compute_score(result)
        before = result.safety_score
        apply_market_scoring(result)
        assert result.safety_score == before
        assert result.risk_level == "medium"

    def test_breakdown_market_dimension_updates(self) -> None:
        result = self._scored_result(fear_index=20.0, deployer_freshness="new")
        compute_score(result)
        apply_market_scoring(result)
        assert isinstance(result.breakdown, dict)
        market = result.breakdown.get("market") or {}
        flags = market.get("flags") or []
        assert "MARKET_FEAR_NEW_TOKEN" in flags


class TestExploitPenaltyGolden:
    """TOP-100 #19 golden contract: -5 per exploit, capped at -10.

    Regression: the penalty used min() of two negatives, which always
    produced the -10 cap even for a single exploit.
    """

    def _delta(self, exploits: int) -> float:
        result = ScanResult(token_address="0xTEST", chain="solana")
        result.market_context = _ctx(recent_exploits=exploits)
        compute_score(result)
        before = result.safety_score
        apply_market_scoring(result)
        return before - result.safety_score

    def test_single_exploit_costs_five_not_ten(self) -> None:
        assert self._delta(1) == pytest.approx(5.0)

    def test_two_exploits_hit_the_cap(self) -> None:
        assert self._delta(2) == pytest.approx(10.0)

    def test_three_exploits_still_capped_at_ten(self) -> None:
        assert self._delta(3) == pytest.approx(10.0)
