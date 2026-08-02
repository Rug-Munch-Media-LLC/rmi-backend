"""Unit tests for hype_score — C5 composite token momentum score.

Covers ``compute_hype_score()`` with mocked data sources:

  - 4-factor composite (social, volume, holders, price) with spec weights
  - Exponential decay (0.5 per 72h of inactivity)
  - All-factors-unavailable graceful degradation
  - Per-signal breakdown shape
  - Redis fast-cache path
  - Individual signal functions (social velocity, price momentum,
    volume growth, holder growth)

All external dependencies (ClickHouse, Redis, trending feed, price oracle)
are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.hype_score import (
    _holder_growth,
    _price_momentum,
    _social_velocity,
    _volume_growth,
    clear_cache,
    compute_hype_score,
)

_TOKEN = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class FakeCHClient:
    """Sync ClickHouse stand-in dispatching on hype_score SQL fragments."""

    def __init__(
        self,
        volume: list[tuple] | None = None,
        holders: list[tuple] | None = None,
        last_activity: list[tuple] | None = None,
    ) -> None:
        self.volume = volume or []
        self.holders = holders or []
        self.last_activity = last_activity or []
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if "sumIf" in sql and "INTERVAL 15 MINUTE" in sql:
            return self.volume
        if "uniqIf" in sql and "INTERVAL 1 DAY" in sql:
            return self.holders
        if "max(ts)" in sql:
            return self.last_activity
        return []


def _mock_ch_hype(fake: FakeCHClient):
    return patch("app.domains.scanner.engines.hype_score._get_ch_client", return_value=fake)


def _no_redis_get():
    return patch("app.domains.scanner.engines.hype_score._redis_get", AsyncMock(return_value=None))


def _no_redis_set():
    return patch("app.domains.scanner.engines.hype_score._redis_set", AsyncMock(return_value=True))


def _social_100():
    return patch(
        "app.domains.scanner.engines.hype_score._social_velocity",
        AsyncMock(return_value={"score": 100.0, "trending_hit": True, "detail": "mock"}),
    )


def _price_100():
    return patch(
        "app.domains.scanner.engines.hype_score._price_momentum",
        AsyncMock(return_value={"score": 100.0, "change_24h_pct": 100.0, "detail": "mock"}),
    )


def _raw_usd_scaled(scale: float = 0.001):
    """Oracle-free USD conversion: raw * scale."""
    return patch(
        "app.domains.scanner.engines.hype_score._raw_to_usd",
        AsyncMock(side_effect=lambda t, c, raw: raw * scale),
    )


class TestComputeHypeScore:
    """Integration tests for compute_hype_score against a fake ClickHouse."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_composite_4_factor_weighted_and_breakdown(self) -> None:
        """Weighted composite = 0.4*S + 0.3*V + 0.2*H + 0.1*P, breakdown returned."""
        fake = FakeCHClient(
            volume=[(1000.0, 8000.0)],  # 15m raw, 4h raw
            holders=[(50, 20)],  # today, previous day
            last_activity=[(datetime.now(UTC) - timedelta(minutes=1),)],  # active now
        )
        with (
            _mock_ch_hype(fake),
            _social_100(),
            _price_100(),
            _raw_usd_scaled(),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await compute_hype_score(_TOKEN, "ethereum")

        assert out["result"] == "scanned"
        # volume: ratio = 1.0 / (8.0/16) = 2.0 -> score 50
        assert out["signals"]["volume_growth"]["ratio"] == pytest.approx(2.0, abs=0.01)
        assert out["signals"]["volume_growth"]["score"] == pytest.approx(50.0, abs=0.1)
        # holder: ratio = 50/20 = 2.5 -> score 100
        assert out["signals"]["holder_growth"]["ratio"] == pytest.approx(2.5, abs=0.01)
        assert out["signals"]["holder_growth"]["score"] == pytest.approx(100.0, abs=0.1)
        # composite = 40 + 15 + 20 + 10 = 85, decay ~1.0
        assert out["hype_score"] == pytest.approx(85.0, abs=0.5)
        assert out["decay_factor"] == pytest.approx(1.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_per_signal_breakdown_shape(self) -> None:
        """The signals dict exposes all four factors plus weights."""
        fake = FakeCHClient(volume=[], holders=[], last_activity=[])
        with (
            _mock_ch_hype(fake),
            patch(
                "app.domains.scanner.engines.hype_score._social_velocity",
                AsyncMock(return_value={"score": 30.0, "trending_hit": True, "detail": "mock"}),
            ),
            patch(
                "app.domains.scanner.engines.hype_score._price_momentum",
                AsyncMock(return_value={"score": 70.0, "change_24h_pct": 40.0, "detail": "mock"}),
            ),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await compute_hype_score(_TOKEN, "ethereum")

        for key in ("social_velocity", "volume_growth", "holder_growth", "price_momentum"):
            assert key in out["signals"]
            assert "score" in out["signals"][key]
        assert out["weights"] == {"social": 0.4, "volume": 0.3, "holder": 0.2, "price": 0.1}

    @pytest.mark.asyncio
    async def test_exponential_decay_halves_per_72h(self) -> None:
        """72h of inactivity halves the composite (decay factor ~0.5)."""
        fake = FakeCHClient(
            volume=[(1000.0, 8000.0)],
            holders=[(50, 20)],
            last_activity=[(datetime.now(UTC) - timedelta(hours=72),)],
        )
        with (
            _mock_ch_hype(fake),
            _social_100(),
            _price_100(),
            _raw_usd_scaled(),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await compute_hype_score(_TOKEN, "ethereum")

        # Same 85-composite as the fresh case, but decayed by 0.5
        assert out["inactivity_hours"] == pytest.approx(72.0, abs=1.0)
        assert out["decay_factor"] == pytest.approx(0.5, abs=0.01)
        assert out["hype_score"] == pytest.approx(42.5, abs=1.0)

    @pytest.mark.asyncio
    async def test_all_signals_unavailable_graceful_degradation(self) -> None:
        """No data anywhere -> scanned result with zeros, never raises."""
        fake = FakeCHClient(volume=[], holders=[], last_activity=[])
        with (
            _mock_ch_hype(fake),
            patch(
                "app.domains.scanner.engines.hype_score._social_velocity",
                AsyncMock(return_value={"score": 0.0, "detail": "no_trending_data"}),
            ),
            patch(
                "app.domains.scanner.engines.hype_score._price_momentum",
                AsyncMock(return_value={"score": 0.0, "detail": "price_oracle_unavailable"}),
            ),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await compute_hype_score(_TOKEN, "ethereum")

        assert out["result"] == "scanned"
        assert out["hype_score"] == 0
        assert out["decay_factor"] == 1.0
        assert out["signals"]["volume_growth"]["detail"] == "no_own_volume_data"
        assert out["signals"]["holder_growth"]["detail"] == "no_holder_data"

    @pytest.mark.asyncio
    async def test_error_when_token_missing(self) -> None:
        """Missing token address -> error result."""
        with _mock_ch_hype(FakeCHClient()):
            out = await compute_hype_score("", "ethereum")
        assert out["result"] == "error"
        assert out["hype_score"] == 0

    @pytest.mark.asyncio
    async def test_redis_cache_hit_skips_computation(self) -> None:
        """A cached score is returned without running any computation."""
        cached = {
            "check": "hype_score",
            "result": "scanned",
            "token_address": _TOKEN,
            "chain": "ethereum",
            "hype_score": 42.0,
            "signals": {},
        }
        with (
            patch("app.domains.scanner.engines.hype_score._redis_get", AsyncMock(return_value=cached)),
            patch("app.domains.scanner.engines.hype_score._compute_hype", AsyncMock()) as compute,
        ):
            out = await compute_hype_score(_TOKEN, "ethereum")
        assert out == cached
        compute.assert_not_awaited()


class TestSignalComponents:
    """Unit tests for the four individual signal functions."""

    @pytest.mark.asyncio
    async def test_social_velocity_trending_hit(self) -> None:
        """A token in the trending feed earns a positive social score."""
        trending = {
            "tokens": [
                {
                    "address": _TOKEN,
                    "trending_score": 5.0,
                    "volume_24h": 100_000,
                    "price_change_24h": 10.0,
                }
            ]
        }
        with patch("app.unified_scanner.trending.get_trending_tokens", AsyncMock(return_value=trending)):
            out = await _social_velocity(_TOKEN, "ethereum")
        assert out["trending_hit"] is True
        assert out["score"] == pytest.approx(73.2, abs=0.1)

    @pytest.mark.asyncio
    async def test_social_velocity_not_in_feed(self) -> None:
        """A token missing from the feed scores zero with no hit."""
        trending = {"tokens": [{"address": "0xother", "trending_score": 9.0, "volume_24h": 1, "price_change_24h": 0}]}
        with patch("app.unified_scanner.trending.get_trending_tokens", AsyncMock(return_value=trending)):
            out = await _social_velocity(_TOKEN, "ethereum")
        assert out["trending_hit"] is False
        assert out["score"] == 0.0
        assert out["detail"] == "not_in_trending_feed"

    @pytest.mark.asyncio
    async def test_social_velocity_trending_feed_failure(self) -> None:
        """A failing trending feed degrades to zero, not an exception."""
        with patch(
            "app.unified_scanner.trending.get_trending_tokens",
            AsyncMock(side_effect=RuntimeError("feed down")),
        ):
            out = await _social_velocity(_TOKEN, "ethereum")
        assert out["score"] == 0.0
        assert out["trending_hit"] is False

    @pytest.mark.asyncio
    async def test_price_momentum_maps_change_to_score(self) -> None:
        """+100% -> 100, -100% -> 0, 0% -> 50 via the oracle."""
        with patch(
            "app.domains.scanner.engines.price_oracle.get_token_price",
            AsyncMock(return_value={"price_usd": 2.0, "price_change_24h_pct": 100.0, "source": "mock"}),
        ):
            out = await _price_momentum(_TOKEN, "ethereum")
        assert out["score"] == 100.0
        assert out["change_24h_pct"] == 100.0

        with patch(
            "app.domains.scanner.engines.price_oracle.get_token_price",
            AsyncMock(return_value={"price_usd": 0.5, "price_change_24h_pct": -100.0, "source": "mock"}),
        ):
            out = await _price_momentum(_TOKEN, "ethereum")
        assert out["score"] == 0.0

        with patch(
            "app.domains.scanner.engines.price_oracle.get_token_price",
            AsyncMock(return_value={"price_usd": 1.0, "price_change_24h_pct": 0.0, "source": "mock"}),
        ):
            out = await _price_momentum(_TOKEN, "ethereum")
        assert out["score"] == 50.0

    @pytest.mark.asyncio
    async def test_price_momentum_oracle_failure(self) -> None:
        """An unavailable price oracle yields a zero score, not an exception."""
        with patch(
            "app.domains.scanner.engines.price_oracle.get_token_price",
            AsyncMock(side_effect=TimeoutError("oracle slow")),
        ):
            out = await _price_momentum(_TOKEN, "ethereum")
        assert out["score"] == 0.0
        assert out["detail"] == "price_oracle_unavailable"

    @pytest.mark.asyncio
    async def test_volume_growth_ratio_and_score(self) -> None:
        """15m activity vs 4h baseline produces the documented ratio/score."""
        fake = FakeCHClient(volume=[(1000.0, 8000.0)])
        with _mock_ch_hype(fake), _raw_usd_scaled():
            out = await _volume_growth(_TOKEN, "ethereum")
        # short 1.0 USD, baseline 8.0/16 = 0.5 -> ratio 2.0 -> score 50
        assert out["ratio"] == pytest.approx(2.0, abs=0.01)
        assert out["score"] == pytest.approx(50.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_holder_growth_ratio_and_score(self) -> None:
        """Today vs previous-day unique receivers produces the ratio/score."""
        fake = FakeCHClient(holders=[(50, 20)])
        with _mock_ch_hype(fake):
            out = await _holder_growth(_TOKEN, "ethereum")
        assert out["ratio"] == pytest.approx(2.5, abs=0.01)
        assert out["score"] == pytest.approx(100.0, abs=0.1)


if __name__ == "__main__":
    pytest.main([__file__])
