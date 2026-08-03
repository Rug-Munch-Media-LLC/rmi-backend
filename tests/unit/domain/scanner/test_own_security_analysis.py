"""Tests for own_security_analysis — ClickHouse-powered GoPlus replacement.

Tests chain normalization, pool lookup, caching utilities,
and the main get_own_security API with mocked ClickHouse queries.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.domains.scanner.engines.own_security_analysis import (
    _SECURITY_CACHE,
    _SECURITY_CACHE_MAX,
    _SECURITY_CACHE_TTL,
    _cache_security,
    _get_pools_for_token,
    _load_pair_cache,
    _normalize_chain,
    clear_cache,
    get_cache_stats,
    get_own_security,
)

# ═══════════════════════════════════════════════════════════════════
# _normalize_chain
# ═══════════════════════════════════════════════════════════════════


class TestNormalizeChain:
    """Tests for _normalize_chain — chain name normalization."""

    def test_normalizes_ethereum_variants(self):
        """Ethereum variants ('eth', 'mainnet') should map to 'ethereum'."""
        assert _normalize_chain("ethereum") == "ethereum"
        assert _normalize_chain("eth") == "ethereum"
        assert _normalize_chain("mainnet") == "ethereum"

    def test_normalizes_bsc_variants(self):
        """BSC variants ('bnb', 'binance') should map to 'bsc'."""
        assert _normalize_chain("bsc") == "bsc"
        assert _normalize_chain("bnb") == "bsc"
        assert _normalize_chain("binance") == "bsc"

    def test_normalizes_polygon_variants(self):
        """Polygon variants ('matic') should map to 'polygon'."""
        assert _normalize_chain("polygon") == "polygon"
        assert _normalize_chain("matic") == "polygon"

    def test_normalizes_l2_variants(self):
        """L2 chains should map correctly."""
        assert _normalize_chain("arbitrum") == "arbitrum"
        assert _normalize_chain("arb") == "arbitrum"
        assert _normalize_chain("optimism") == "optimism"
        assert _normalize_chain("op") == "optimism"
        assert _normalize_chain("base") == "base"

    def test_normalizes_avalanche_variants(self):
        """Avalanche variants ('avax') should map to 'avalanche'."""
        assert _normalize_chain("avalanche") == "avalanche"
        assert _normalize_chain("avax") == "avalanche"

    def test_normalizes_fantom_variants(self):
        """Fantom variants ('ftm') should map to 'fantom'."""
        assert _normalize_chain("fantom") == "fantom"
        assert _normalize_chain("ftm") == "fantom"

    def test_passes_through_unknown_chain(self):
        """Unknown chains should pass through lowercased."""
        assert _normalize_chain("solana") == "solana"
        assert _normalize_chain("UNKNOWN") == "unknown"

    def test_case_insensitive(self):
        """Normalization should be case-insensitive."""
        assert _normalize_chain("ETH") == "ethereum"
        assert _normalize_chain("BSC") == "bsc"
        assert _normalize_chain("ArBiTrUm") == "arbitrum"


# ═══════════════════════════════════════════════════════════════════
# _get_pools_for_token
# ═══════════════════════════════════════════════════════════════════


class TestGetPoolsForToken:
    """Tests for _get_pools_for_token — reverse pool lookup."""

    def test_finds_token_in_pool_zero(self):
        """Should find pools where token is token0."""
        pair_cache = {
            "0xpool1": ("0xtokenA", "0xtokenB"),
            "0xpool2": ("0xtokenC", "0xtokenD"),
        }
        pools = _get_pools_for_token(pair_cache, "0xtokenA")
        assert pools == ["0xpool1"]

    def test_finds_token_in_pool_one(self):
        """Should find pools where token is token1."""
        pair_cache = {
            "0xpool1": ("0xtokenA", "0xtokenB"),
        }
        pools = _get_pools_for_token(pair_cache, "0xtokenB")
        assert pools == ["0xpool1"]

    def test_finds_token_in_multiple_pools(self):
        """Should find all pools containing the token."""
        pair_cache = {
            "0xpool1": ("0xtokenA", "0xtokenB"),
            "0xpool2": ("0xtokenA", "0xtokenC"),
            "0xpool3": ("0xtokenD", "0xtokenE"),
        }
        pools = _get_pools_for_token(pair_cache, "0xtokenA")
        assert sorted(pools) == ["0xpool1", "0xpool2"]

    def test_returns_empty_for_unknown_token(self):
        """Should return empty list for unknown tokens."""
        result = _get_pools_for_token({}, "0xunknown")
        assert result == []

    def test_case_insensitive_matching(self):
        """Pool lookup should be case-insensitive."""
        pair_cache = {
            "0xpool1": ("0xTOKENa", "0xtokenb"),
        }
        pools = _get_pools_for_token(pair_cache, "0xtokena")
        assert pools == ["0xpool1"]

    def test_empty_cache_returns_empty(self):
        """Empty cache should return empty list."""
        result = _get_pools_for_token({}, "0xtokenA")
        assert result == []


# ═══════════════════════════════════════════════════════════════════
# _load_pair_cache
# ═══════════════════════════════════════════════════════════════════


class TestLoadPairCache:
    """Tests for _load_pair_cache — JSON file cache loading."""

    def setup_method(self):
        """Clear the module-level pair cache before each test."""
        from app.domains.scanner.engines import own_security_analysis as osa

        osa._ch_pair_cache.clear()

    def test_returns_empty_for_missing_file(self):
        """Missing cache file should return empty dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "pair_tokens_ethereum.json"
            assert not cache_path.exists()
            with patch(
                "app.domains.scanner.engines.own_security_analysis._PAIR_CACHE_DIR",
                Path(tmpdir),
            ):
                result = _load_pair_cache("ethereum")
                assert result == {}

    def test_loads_valid_json_file(self):
        """Valid cache file should be loaded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "pair_tokens_ethereum.json"
            data = {
                "0xpool1": ["0xtokenA", "0xtokenB"],
                "0xpool2": ["0xtokenC", "0xtokenD"],
            }
            cache_path.write_text(json.dumps(data))
            with patch(
                "app.domains.scanner.engines.own_security_analysis._PAIR_CACHE_DIR",
                Path(tmpdir),
            ):
                result = _load_pair_cache("ethereum")
                assert result == {
                    "0xpool1": ("0xtokenA", "0xtokenB"),
                    "0xpool2": ("0xtokenC", "0xtokenD"),
                }

    def test_handles_corrupted_json_gracefully(self):
        """Corrupted cache file should return empty dict without crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "pair_tokens_bsc.json"
            cache_path.write_text("{invalid json}")
            with patch(
                "app.domains.scanner.engines.own_security_analysis._PAIR_CACHE_DIR",
                Path(tmpdir),
            ):
                result = _load_pair_cache("bsc")
                assert result == {}

    def test_caches_loaded_data(self):
        """Loaded cache should be cached in memory for subsequent calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "pair_tokens_ethereum.json"
            data = {"0xpool1": ["0xtokenA", "0xtokenB"]}
            cache_path.write_text(json.dumps(data))
            from app.domains.scanner.engines import own_security_analysis as osa

            # Clear the module-level cache first
            osa._ch_pair_cache.clear()

            with patch(
                "app.domains.scanner.engines.own_security_analysis._PAIR_CACHE_DIR",
                Path(tmpdir),
            ):
                # First call should load from file
                result1 = _load_pair_cache("ethereum")
                assert result1 == {"0xpool1": ("0xtokenA", "0xtokenB")}

                # Delete the file, second call should return cached copy
                cache_path.unlink()
                result2 = _load_pair_cache("ethereum")
                assert result2 == {"0xpool1": ("0xtokenA", "0xtokenB")}


# ═══════════════════════════════════════════════════════════════════
# Cache utilities
# ═══════════════════════════════════════════════════════════════════


class TestCacheUtilities:
    """Tests for _cache_security, get_cache_stats, clear_cache."""

    def setup_method(self):
        clear_cache()

    def test_clear_cache_empties_store(self):
        """clear_cache should empty the in-memory cache."""
        _cache_security("test:key", {"result": "ok"})
        assert len(_SECURITY_CACHE) >= 1
        clear_cache()
        assert len(_SECURITY_CACHE) == 0

    def test_get_cache_stats_returns_expected_keys(self):
        """get_cache_stats should return cache metadata."""
        stats = get_cache_stats()
        assert stats["cache_size"] == 0
        assert stats["cache_max"] == _SECURITY_CACHE_MAX
        assert stats["cache_ttl_seconds"] == _SECURITY_CACHE_TTL
        assert "clickhouse_connected" in stats

    def test_cache_stats_reflects_current_size(self):
        """Cache stats should update as items are added."""
        clear_cache()
        stats = get_cache_stats()
        assert stats["cache_size"] == 0
        _cache_security("key1:eth", {"result": "a"})
        assert get_cache_stats()["cache_size"] == 1
        _cache_security("key2:eth", {"result": "b"})
        assert get_cache_stats()["cache_size"] == 2

    def test_cache_evicts_oldest_when_full(self):
        """Cache should evict oldest entries when over max."""
        clear_cache()
        # Fill cache past max
        for i in range(_SECURITY_CACHE_MAX + 10):
            _cache_security(f"key{i}:eth", {"result": str(i)})
        # Cache size should not exceed max
        assert len(_SECURITY_CACHE) <= _SECURITY_CACHE_MAX

    def test_cache_adds_timestamp(self):
        """Cached items should have a _cached_at timestamp."""
        clear_cache()
        _cache_security("test:ts", {"result": "ok"})
        assert "_cached_at" in _SECURITY_CACHE["test:ts"]
        assert isinstance(_SECURITY_CACHE["test:ts"]["_cached_at"], float)

    def test_clear_cache_does_not_raise_when_empty(self):
        """clear_cache should not raise when already empty."""
        clear_cache()
        clear_cache()  # second call should be safe


# ═══════════════════════════════════════════════════════════════════
# get_own_security
# ═══════════════════════════════════════════════════════════════════


class TestGetOwnSecurity:
    """Tests for get_own_security — the main API."""

    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_structure(self):
        """get_own_security should return a dict with security indicator keys."""
        clear_cache()
        with patch(
            "app.domains.scanner.engines.own_security_analysis._get_ch_client",
            return_value=None,  # No ClickHouse = graceful fallback with defaults
        ):
            result = await get_own_security(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
            )
        assert isinstance(result, dict)
        assert result["source"] == "own"
        assert "is_honeypot" in result
        assert "owner_address" in result
        assert "buy_tax_pct" in result
        assert "sell_tax_pct" in result
        assert "holder_count" in result
        assert "risk_score" in result
        assert "age_hours" in result
        assert "confidence" in result
        assert "result" in result
        # With no CH, it should return scanned with defaults, not crash
        assert result["result"] == "scanned"
        assert result["risk_score"] == 0
        assert result["is_honeypot"] is None

    @pytest.mark.asyncio
    async def test_returns_unavailable_on_error(self):
        """get_own_security should return 'unavailable' result on error."""
        with patch(
            "app.domains.scanner.engines.own_security_analysis._get_ch_client",
            side_effect=RuntimeError("CH unavailable"),
        ):
            result = await get_own_security("0xdead", "ethereum")
        assert result["result"] == "unavailable"

    @pytest.mark.asyncio
    async def test_handles_unknown_token(self):
        """Should gracefully handle tokens with no on-chain data."""
        clear_cache()
        with patch(
            "app.domains.scanner.engines.own_security_analysis._get_ch_client",
            return_value=None,
        ):
            result = await get_own_security(
                "0x0000000000000000000000000000000000000001",
                "ethereum",
            )
        assert isinstance(result, dict)
        assert result["result"] == "scanned"

    @pytest.mark.asyncio
    async def test_force_fresh_bypasses_cache(self):
        """force_fresh=True should bypass the cache."""
        clear_cache()
        with patch(
            "app.domains.scanner.engines.own_security_analysis._get_ch_client",
            return_value=None,
        ):
            result1 = await get_own_security(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
            )
            result2 = await get_own_security(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
                force_fresh=True,
            )
        assert isinstance(result1, dict)
        assert isinstance(result2, dict)

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_result(self):
        """Cached results should be returned without re-querying."""
        clear_cache()
        call_count = 0
        mock_client = MagicMock()
        mock_client.execute.return_value = []

        def tracking_client():
            nonlocal call_count
            call_count += 1
            return mock_client

        with (
            patch(
                "app.domains.scanner.engines.own_security_analysis._get_ch_client",
                side_effect=tracking_client,
            ),
            patch(
                "app.domains.scanner.engines.own_security_analysis._load_pair_cache",
                return_value={},
            ),
        ):
            result1 = await get_own_security("0xcached_token", "ethereum")
            first_count = call_count
            # Result should be scanned (data returned None for each query = graceful)
            assert result1["result"] == "scanned"

            # Second call should hit cache — no more client connections
            result2 = await get_own_security("0xcached_token", "ethereum")
            assert call_count == first_count  # Should use cache
            assert result2["result"] == "scanned"

    @pytest.mark.asyncio
    async def test_lowercases_token_address(self):
        """Token address should be lowercased."""
        clear_cache()
        with patch(
            "app.domains.scanner.engines.own_security_analysis._get_ch_client",
            return_value=None,
        ):
            result = await get_own_security(
                "0xABC123DEF456ABC123DEF456ABC123DEF4567890",
                "ethereum",
            )
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_risk_score_is_capped_at_100(self):
        """Risk score should never exceed 100."""
        clear_cache()
        mock_client = MagicMock()
        # Realistic driver failure: clickhouse_driver raises typed errors (or
        # RuntimeError for connection/init failures), never a bare Exception.
        # The scanner engines narrow their except clauses to those types, so a
        # bare Exception mock would no longer be caught.
        mock_client.execute.side_effect = RuntimeError("No data")

        with patch(
            "app.domains.scanner.engines.own_security_analysis._get_ch_client",
            return_value=mock_client,
        ):
            result = await get_own_security(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
            )
        assert result["risk_score"] <= 100
        assert result["risk_score"] >= 0


# ═══════════════════════════════════════════════════════════════════
# Honeypot detection logic
# ═══════════════════════════════════════════════════════════════════


class TestHoneypotDetection:
    """Tests for honeypot detection in get_own_security."""

    @pytest.mark.asyncio
    async def test_high_buy_low_sell_detects_honeypot(self):
        """Significant buy activity with zero sells should flag honeypot."""
        clear_cache()
        mock_client = MagicMock()

        def mock_execute(query, params=None, **kwargs):
            if "deployer_events" in query:
                return []  # No deployer data
            if "evm_transfers" in query:
                # [in_count, out_count, unique_senders, unique_recipients]
                return [(15, 0, 10, 5)]
            if "pair" in query.lower() or "group by" in query.lower():
                return {}  # No DEX data from _load_pair_cache returning {}
            return []

        mock_client.execute = mock_execute

        with (
            patch(
                "app.domains.scanner.engines.own_security_analysis._get_ch_client",
                return_value=mock_client,
            ),
            patch(
                "app.domains.scanner.engines.own_security_analysis._load_pair_cache",
                return_value={},
            ),
        ):
            result = await get_own_security(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
            )
        assert result["is_honeypot"] is True
        assert result["risk_score"] >= 40

    @pytest.mark.asyncio
    async def test_balanced_buy_sell_not_honeypot(self):
        """Balanced buy/sell activity should not flag honeypot."""
        clear_cache()
        mock_client = MagicMock()

        def mock_execute(query, params=None, **kwargs):
            if "deployer_events" in query:
                return []
            if "evm_transfers" in query:
                return [(20, 18, 15, 12)]  # Balanced in/out
            return []

        mock_client.execute = mock_execute

        with (
            patch(
                "app.domains.scanner.engines.own_security_analysis._get_ch_client",
                return_value=mock_client,
            ),
            patch(
                "app.domains.scanner.engines.own_security_analysis._load_pair_cache",
                return_value={},
            ),
        ):
            result = await get_own_security(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
            )
        assert result["is_honeypot"] is False

    @pytest.mark.asyncio
    async def test_low_holders_increases_risk(self):
        """Few unique holders should increase risk score."""
        clear_cache()
        mock_client = MagicMock()

        def mock_execute(query, params=None, **kwargs):
            if "deployer_events" in query:
                return []
            if "evm_transfers" in query:
                return [(5, 3, 2, 2)]  # Only 2 unique holders
            return []

        mock_client.execute = mock_execute

        with (
            patch(
                "app.domains.scanner.engines.own_security_analysis._get_ch_client",
                return_value=mock_client,
            ),
            patch(
                "app.domains.scanner.engines.own_security_analysis._load_pair_cache",
                return_value={},
            ),
        ):
            result = await get_own_security(
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ethereum",
            )
        assert result["holder_count"] == 2
        assert result["risk_score"] >= 15
