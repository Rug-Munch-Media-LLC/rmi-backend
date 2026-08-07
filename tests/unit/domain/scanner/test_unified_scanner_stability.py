"""Tests for scanner score stability work (2026-08-07).

Covers:
  1. Result caching — a token scanned within the last hour is served from
     the Redis result cache with ``cached=True`` instead of re-scoring.
  2. Deterministic scoring — the same inputs always produce the same score
     (no RNG, no iteration-order dependence).
  3. Stability field — how many recent scans agree on the risk level.
  4. ``_from_legacy_dict`` round-trip — the cached legacy dict hydrates back
     into a full ``ScanResult``.

Run: pytest tests/unit/domain/scanner/test_unified_scanner_stability.py -v
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domains.scanner.unified._legacy import _from_legacy_dict, _to_legacy_dict
from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.scanner import UnifiedTokenScanner
from app.domains.scanner.unified.scoring import compute_score


def _result(**overrides: object) -> ScanResult:
    """Build a minimal ScanResult for stability/cache tests."""
    defaults: dict[str, object] = {
        "token_address": "0xCACHETEST",
        "chain": "ethereum",
        "symbol": "TST",
        "name": "Cache Test",
        "safety_score": 65.0,
        "risk_level": "low",
        "risk_flags": ["LP_LOCK_LOW"],
        "green_flags": ["HOLDER_DISTRIBUTED"],
        "modules_run": ["token_price", "token_security"],
    }
    defaults.update(overrides)
    return ScanResult(**defaults)  # type: ignore[arg-type]


class FakeRedis:
    """Minimal in-memory async Redis stand-in."""

    def __init__(self, data: dict[str, str] | None = None) -> None:
        self._data: dict[str, str] = dict(data or {})

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = value

    def seed(self, key: str, value: str) -> None:
        self._data[key] = value


def _patch_redis(fake: FakeRedis):
    """Patch app.core.redis.get_redis_async to return the fake.

    ``get_redis_async`` is a SYNC function that returns an async Redis client
    (the scanner calls ``r = get_redis_async()`` without awaiting), so we
    patch it with a plain Mock, not an AsyncMock.
    """
    return patch("app.core.redis.get_redis_async", new=Mock(return_value=fake))


class TestResultCaching:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_result_with_flag(self) -> None:
        """A token scanned within the hour is served from cache (cached=True)."""
        cached_legacy = _to_legacy_dict(_result())
        fake = FakeRedis({"sentinelscan:ethereum:0xCACHETEST": json.dumps(cached_legacy)})
        scanner = UnifiedTokenScanner()

        with (
            _patch_redis(fake),
            patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})) as fetch,
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock()),
        ):
            result = await scanner.scan("0xCACHETEST", chain="ethereum", tier="free")

        assert result.cached is True
        assert result.background_status == "complete"
        assert result.safety_score == 65.0
        assert result.risk_level == "low"
        # No fresh scan was performed — cache short-circuits the fetch path
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_falls_through_to_scan(self) -> None:
        """No cache entry → full scan runs (cache miss path)."""
        fake = FakeRedis()
        scanner = UnifiedTokenScanner()

        with (
            _patch_redis(fake),
            patch.object(
                scanner,
                "_fetch_batch",
                new=AsyncMock(return_value={"token_price": {"price_usd": 1.0, "source": "own"}}),
            ) as fetch,
            patch.object(scanner, "_run_wallet_analysis", new=AsyncMock()),
            patch.object(scanner, "_run_event_correlation", new=AsyncMock()),
            patch.object(scanner, "_run_news_sentiment", new=AsyncMock()),
            patch.object(scanner, "_run_sentinel_phase", new=AsyncMock()),
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock()),
        ):
            result = await scanner.scan("0xCACHETEST", chain="ethereum", tier="free")

        assert result.cached is False
        assert result.token_address == "0xCACHETEST"
        assert fetch.call_count >= 1

    @pytest.mark.asyncio
    async def test_elite_tier_bypasses_cache(self) -> None:
        """Elite/admin (deep SENTINEL) scans always run fresh — never cache-hit."""
        cached_legacy = _to_legacy_dict(_result())
        fake = FakeRedis({"sentinelscan:ethereum:0xCACHETEST": json.dumps(cached_legacy)})
        scanner = UnifiedTokenScanner()

        with (
            _patch_redis(fake),
            patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})) as fetch,
            patch.object(scanner, "_run_wallet_analysis", new=AsyncMock()),
            patch.object(scanner, "_run_event_correlation", new=AsyncMock()),
            patch.object(scanner, "_run_news_sentiment", new=AsyncMock()),
            patch.object(scanner, "_run_sentinel_phase", new=AsyncMock()),
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock()),
        ):
            result = await scanner.scan("0xCACHETEST", chain="ethereum", tier="elite")

        assert result.cached is False
        assert fetch.call_count >= 1


class TestDeterministicScoring:
    def test_same_inputs_same_score(self) -> None:
        """compute_score is a pure function of its inputs — no RNG."""
        result_a = _result(
            liquidity={"locked_pct": 95},
            holders={"top_10_pct": 20},
            dev_reputation={"risk_score": 10},
            volume_auth={"fake_volume_pct": 5},
            modules_run=["liquidity", "holders", "deployer", "volume"],
        )
        result_b = _result(
            liquidity={"locked_pct": 95},
            holders={"top_10_pct": 20},
            dev_reputation={"risk_score": 10},
            volume_auth={"fake_volume_pct": 5},
            modules_run=["liquidity", "holders", "deployer", "volume"],
        )

        out_a = compute_score(result_a)
        out_b = compute_score(result_b)

        assert out_a["score"] == out_b["score"]
        assert out_a["risk_level"] == out_b["risk_level"]
        assert result_a.risk_flags == result_b.risk_flags
        assert result_a.breakdown == result_b.breakdown

    def test_weights_are_constant(self) -> None:
        """The five dimension weights are fixed constants (no drift)."""
        from app.domains.scanner.unified.scoring import DIMENSION_WEIGHTS

        assert DIMENSION_WEIGHTS == {
            "security": 35,
            "liquidity": 25,
            "holders": 15,
            "deployer": 15,
            "market": 10,
        }
        assert sum(DIMENSION_WEIGHTS.values()) == 100


class TestStabilityField:
    @pytest.mark.asyncio
    async def test_cache_hit_attaches_stability_summary(self) -> None:
        """Stability (recent-scan agreement) rides along on cache hits."""
        cached_legacy = _to_legacy_dict(_result(risk_level="low"))
        fake = FakeRedis(
            {
                "sentinelscan:ethereum:0xCACHETEST": json.dumps(cached_legacy),
                "scanner:stability:ethereum:0xCACHETEST": json.dumps(["low", "low", "medium"]),
            }
        )
        scanner = UnifiedTokenScanner()

        with (
            _patch_redis(fake),
            patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})),
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock()),
        ):
            result = await scanner.scan("0xCACHETEST", chain="ethereum", tier="free")

        assert result.stability is not None
        assert result.stability["scans"] == 3
        assert result.stability["level"] == "low"
        assert result.stability["agreeing"] == 2
        assert result.stability["agree_pct"] == 67

    @pytest.mark.asyncio
    async def test_no_stability_history_returns_none(self) -> None:
        """No recorded history → stability is None (graceful)."""
        cached_legacy = _to_legacy_dict(_result())
        fake = FakeRedis({"sentinelscan:ethereum:0xCACHETEST": json.dumps(cached_legacy)})
        scanner = UnifiedTokenScanner()

        with (
            _patch_redis(fake),
            patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})),
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock()),
        ):
            result = await scanner.scan("0xCACHETEST", chain="ethereum", tier="free")

        assert result.stability is None


class TestCacheRoundTrip:
    def test_legacy_dict_round_trips_full_result(self) -> None:
        """_to_legacy_dict → _from_legacy_dict preserves the API surface."""
        original = _result(
            breakdown={"security": {"score": 20, "max": 35, "delta": -15, "flags": ["CR01"]}},
            evidence={"security": {"checks": {"CR01": {"status": "fail"}}}},
            sentinel={
                "free": {"holder_analysis": {"hhi": 2500, "risk_level": "high"}},
                "premium": None,
                "modules_run": ["holder_analysis"],
            },
            news_sentiment={"result": "scanned", "sentiment": "NEUTRAL"},
            enrichment_sources=["price:own", "security:goplus"],
        )
        hydrated = _from_legacy_dict(_to_legacy_dict(original))

        assert hydrated is not None
        assert hydrated.token_address == original.token_address
        assert hydrated.chain == original.chain
        assert hydrated.safety_score == original.safety_score
        assert hydrated.risk_level == original.risk_level
        assert hydrated.risk_flags == original.risk_flags
        assert hydrated.green_flags == original.green_flags
        assert hydrated.breakdown == original.breakdown
        assert hydrated.evidence == original.evidence
        assert hydrated.sentinel == original.sentinel
        assert hydrated.news_sentiment == original.news_sentiment
        assert hydrated.modules_run == original.modules_run
        assert hydrated.enrichment_sources == original.enrichment_sources

    def test_from_legacy_dict_returns_none_for_garbage(self) -> None:
        """Malformed payloads degrade to None (never raise)."""
        assert _from_legacy_dict({}) is None
        assert _from_legacy_dict(None) is None  # type: ignore[arg-type]
        assert _from_legacy_dict({"token_address": ""}) is None
        assert _from_legacy_dict("garbage") is None  # type: ignore[arg-type]
