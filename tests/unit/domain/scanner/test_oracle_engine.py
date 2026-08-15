"""Unit tests for app/domains/scanner/engines/_oracle.py — tiered price oracle.

Covers get_token_price (cache hit, tier fallback, provider failure, invalid
token), get_token_price_at_time (VWAP path + current fallback), get_token_decimals
(resolution order + defaults), get_native_price (wrapped oracle → CoinGecko →
DeFiLlama), and normalize_amount. All external boundaries (adapters, caches,
sources) are monkeypatched — no network, no ClickHouse, no Redis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domains.scanner.engines import _oracle
from app.domains.scanner.engines._oracle import (
    get_native_price,
    get_token_decimals,
    get_token_price,
    get_token_price_at_time,
    normalize_amount,
)

_TIER_NAMES = ("own_dex_events", "dexscreener", "coingecko", "defillama", "geckoterminal", "paraswap", "1inch")


def _mock_tiers(**overrides: object) -> list[tuple[str, AsyncMock]]:
    """Build a full 7-tier ``_TIERS`` replacement with per-tier AsyncMocks.

    ``_oracle._TIERS`` holds direct references to the fetcher functions, so
    patching the defining modules (``_sources`` / ``_price_adapters``) does
    NOT affect the tier loop — the list itself must be swapped. Each tier
    defaults to returning None (skip); ``overrides`` maps a tier name to a
    return value or a ready AsyncMock (for ``side_effect`` setups).
    """
    tiers: dict[str, AsyncMock] = {name: AsyncMock(return_value=None) for name in _TIER_NAMES}
    for name, value in overrides.items():
        tiers[name] = value if isinstance(value, AsyncMock) else AsyncMock(return_value=value)
    return list(tiers.items())


class TestGetTokenPrice:
    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self) -> None:
        assert await get_token_price("", "ethereum") is None

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self) -> None:
        cached = {"price_usd": 1.5, "source": "dexscreener", "timestamp": 1.0, "confidence": 0.7}
        with patch("app.domains.scanner.engines._oracle._get_cached_price", new=AsyncMock(return_value=cached)):
            result = await get_token_price("0xabc", "ethereum")
        assert result is cached

    @pytest.mark.asyncio
    async def test_tier_resolution_and_enrichment(self) -> None:
        tier_result = {
            "price_usd": 2.0,
            "confidence": 0.8,
            "volume_24h_usd": 5000,
            "symbol": "TEST",
            "decimals": 18,
        }
        with (
            patch("app.domains.scanner.engines._oracle._get_cached_price", new=AsyncMock(return_value=None)),
            patch.object(_oracle, "_TIERS", _mock_tiers(dexscreener=tier_result)),
            patch("app.domains.scanner.engines._oracle._cache_set_price", new=AsyncMock()),
        ):
            result = await get_token_price("0xabc", "ethereum")
        assert result is not None
        assert result["source"] == "dexscreener"
        assert result["price_usd"] == 2.0
        assert result["volume_24h_usd"] == 5000
        assert result["symbol"] == "TEST"
        assert result["decimals"] == 18

    @pytest.mark.asyncio
    async def test_zero_price_tier_skipped(self) -> None:
        with (
            patch("app.domains.scanner.engines._oracle._get_cached_price", new=AsyncMock(return_value=None)),
            patch.object(
                _oracle,
                "_TIERS",
                _mock_tiers(dexscreener={"price_usd": 0.0}, coingecko={"price_usd": 3.0, "confidence": 0.5}),
            ),
            patch("app.domains.scanner.engines._oracle._cache_set_price", new=AsyncMock()),
        ):
            result = await get_token_price("0xabc", "ethereum")
        assert result is not None
        assert result["source"] == "coingecko"
        assert result["price_usd"] == 3.0

    @pytest.mark.asyncio
    async def test_provider_exception_falls_through(self) -> None:
        with (
            patch("app.domains.scanner.engines._oracle._get_cached_price", new=AsyncMock(return_value=None)),
            patch.object(
                _oracle,
                "_TIERS",
                _mock_tiers(
                    own_dex_events=AsyncMock(side_effect=ConnectionError("boom")),
                    dexscreener={"price_usd": 4.0},
                ),
            ),
            patch("app.domains.scanner.engines._oracle._cache_set_price", new=AsyncMock()),
        ):
            result = await get_token_price("0xabc", "ethereum")
        assert result is not None
        assert result["source"] == "dexscreener"

    @pytest.mark.asyncio
    async def test_all_tiers_fail_returns_none(self) -> None:
        with (
            patch("app.domains.scanner.engines._oracle._get_cached_price", new=AsyncMock(return_value=None)),
            patch.object(_oracle, "_TIERS", _mock_tiers()),
        ):
            result = await get_token_price("0xabc", "ethereum")
        assert result is None

    @pytest.mark.asyncio
    async def test_force_fresh_skips_cache(self) -> None:
        with (
            patch(
                "app.domains.scanner.engines._oracle._get_cached_price",
                new=AsyncMock(return_value={"price_usd": 9.9}),
            ) as get_cached,
            patch.object(_oracle, "_TIERS", _mock_tiers(own_dex_events={"price_usd": 1.0})),
            patch("app.domains.scanner.engines._oracle._cache_set_price", new=AsyncMock()),
        ):
            result = await get_token_price("0xabc", "ethereum", force_fresh=True)
        get_cached.assert_not_awaited()
        assert result["source"] == "own_dex_events"


class TestGetTokenPriceAtTime:
    @pytest.mark.asyncio
    async def test_vwap_path_when_swaps_exist(self) -> None:
        at = datetime(2026, 1, 1, tzinfo=UTC)
        swaps = [{"swap1": 1}, {"swap2": 2}]
        vwap = {"price_usd": 10.0, "confidence": 0.6}
        with (
            patch(
                "app.domains.scanner.engines._oracle._query_swaps_between",
                new=AsyncMock(return_value=swaps),
            ) as q,
            patch(
                "app.domains.scanner.engines._oracle._vwap_price_from_swaps",
                new=AsyncMock(return_value=vwap),
            ),
        ):
            result = await get_token_price_at_time("0xabc", "ethereum", at)
        assert result is not None
        assert result["source"] == "own_dex_events_at_time"
        assert result["swap_count"] == 2
        assert result["price_usd"] == 10.0
        assert result["window_minutes"] == 30
        # window check: lo/hi passed to query
        assert q.await_args is not None

    @pytest.mark.asyncio
    async def test_current_fallback_when_no_swaps(self) -> None:
        at = datetime(2026, 1, 1, tzinfo=UTC)
        with (
            patch("app.domains.scanner.engines._oracle._query_swaps_between", new=AsyncMock(return_value=[])),
            patch(
                "app.domains.scanner.engines._oracle.get_token_price",
                new=AsyncMock(return_value={"price_usd": 5.0, "source": "dexscreener"}),
            ),
        ):
            result = await get_token_price_at_time("0xabc", "ethereum", at)
        assert result is not None
        assert result["source"] == "current_fallback"
        assert result["confidence"] == 0.3
        assert result["swap_count"] == 0

    @pytest.mark.asyncio
    async def test_fallback_failure_returns_none(self) -> None:
        at = datetime(2026, 1, 1, tzinfo=UTC)
        with (
            patch("app.domains.scanner.engines._oracle._query_swaps_between", new=AsyncMock(return_value=[])),
            patch("app.domains.scanner.engines._oracle.get_token_price", new=AsyncMock(return_value=None)),
        ):
            result = await get_token_price_at_time("0xabc", "ethereum", at)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self) -> None:
        assert await get_token_price_at_time("", "ethereum", datetime.now(UTC)) is None


class TestGetTokenDecimals:
    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self) -> None:
        assert await get_token_decimals("", "ethereum") is None

    @pytest.mark.asyncio
    async def test_default_evm_18(self, monkeypatch) -> None:
        monkeypatch.setattr(_oracle, "_decimals_mem", {})
        with (
            patch("app.domains.scanner.engines._oracle._meta_get", new=AsyncMock(return_value=None)),
            patch(
                "app.domains.scanner.engines._oracle._decimals_from_dexscreener",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.domains.scanner.engines.own_dex_data.format._get_token_decimals",
                new=Mock(return_value=None),
            ),
        ):
            assert await get_token_decimals("0xabc", "ethereum") == 18

    @pytest.mark.asyncio
    async def test_default_solana_9(self, monkeypatch) -> None:
        monkeypatch.setattr(_oracle, "_decimals_mem", {})
        with (
            patch("app.domains.scanner.engines._oracle._meta_get", new=AsyncMock(return_value=None)),
            patch(
                "app.domains.scanner.engines._oracle._decimals_from_dexscreener",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.domains.scanner.engines.own_dex_data.format._get_token_decimals",
                new=Mock(return_value=None),
            ),
        ):
            assert await get_token_decimals("solmint", "solana") == 9

    @pytest.mark.asyncio
    async def test_dexscreener_decimals_used(self, monkeypatch) -> None:
        monkeypatch.setattr(_oracle, "_decimals_mem", {})
        with (
            patch("app.domains.scanner.engines._oracle._meta_get", new=AsyncMock(return_value=None)),
            patch(
                "app.domains.scanner.engines._oracle._decimals_from_dexscreener",
                new=AsyncMock(return_value=6),
            ),
        ):
            assert await get_token_decimals("0xabc", "ethereum") == 6

    @pytest.mark.asyncio
    async def test_meta_decimals_used(self, monkeypatch) -> None:
        monkeypatch.setattr(_oracle, "_decimals_mem", {})
        with patch(
            "app.domains.scanner.engines._oracle._meta_get",
            new=AsyncMock(return_value={"decimals": 8}),
        ):
            assert await get_token_decimals("0xabc", "ethereum") == 8


class TestGetNativePrice:
    @pytest.mark.asyncio
    async def test_wrapped_token_oracle(self, monkeypatch) -> None:
        monkeypatch.setattr(_oracle, "_native_mem", {})
        with patch(
            "app.domains.scanner.engines._oracle.get_token_price",
            new=AsyncMock(return_value={"price_usd": 3000.0, "source": "dexscreener"}),
        ):
            assert await get_native_price("ethereum") == 3000.0

    @pytest.mark.asyncio
    async def test_coingecko_fallback(self, monkeypatch) -> None:
        monkeypatch.setattr(_oracle, "_native_mem", {})
        with (
            patch("app.domains.scanner.engines._oracle.get_token_price", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines._oracle._native_coingecko", new=AsyncMock(return_value=400.0)),
        ):
            assert await get_native_price("bsc") == 400.0

    @pytest.mark.asyncio
    async def test_defillama_fallback(self, monkeypatch) -> None:
        monkeypatch.setattr(_oracle, "_native_mem", {})
        with (
            patch("app.domains.scanner.engines._oracle.get_token_price", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines._oracle._native_coingecko", new=AsyncMock(return_value=0.0)),
            patch("app.domains.scanner.engines._oracle._native_defillama", new=AsyncMock(return_value=150.0)),
        ):
            assert await get_native_price("solana") == 150.0

    @pytest.mark.asyncio
    async def test_all_fail_returns_zero(self, monkeypatch) -> None:
        monkeypatch.setattr(_oracle, "_native_mem", {})
        with (
            patch("app.domains.scanner.engines._oracle.get_token_price", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines._oracle._native_coingecko", new=AsyncMock(return_value=0.0)),
            patch("app.domains.scanner.engines._oracle._native_defillama", new=AsyncMock(return_value=0.0)),
        ):
            assert await get_native_price("fantom") == 0.0


class TestNormalizeAmount:
    @pytest.mark.asyncio
    async def test_invalid_raw_returns_none(self) -> None:
        assert await normalize_amount("0xabc", "ethereum", "not-a-number") is None
        assert await normalize_amount("0xabc", "ethereum", 0) is None
        assert await normalize_amount("0xabc", "ethereum", -5) is None

    @pytest.mark.asyncio
    async def test_usd_conversion(self) -> None:
        with (
            patch(
                "app.domains.scanner.engines._oracle.get_token_price",
                new=AsyncMock(return_value={"price_usd": 2.0, "source": "dexscreener"}),
            ),
            patch("app.domains.scanner.engines._oracle.get_token_decimals", new=AsyncMock(return_value=18)),
        ):
            # 1e18 raw units * $2 / 1e18 = $2
            assert await normalize_amount("0xabc", "ethereum", 10**18) == 2.0

    @pytest.mark.asyncio
    async def test_missing_price_returns_none(self) -> None:
        with patch("app.domains.scanner.engines._oracle.get_token_price", new=AsyncMock(return_value=None)):
            assert await normalize_amount("0xabc", "ethereum", 100) is None
