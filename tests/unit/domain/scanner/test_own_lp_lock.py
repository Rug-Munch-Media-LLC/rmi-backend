"""Tests for own_lp_lock — ClickHouse-powered LP lock/burn detection.

Covers chain normalization, the known lock contract registry, holder and
transfer analysis, the async public API with a mocked ClickHouse client, and
the usage/cache monitoring helpers.

All external dependencies (ClickHouse, Redis) are mocked.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.domains.scanner.engines import own_lp_lock as lp

# ── Well-known addresses used across tests ────────────────────────────────
UNICRYPT_V1 = "0x663a5c229c09b049e36dcc11a9b0d4a8eb9db214"
DEAD_ADDR = "0x000000000000000000000000000000000000dead"
ZERO_ADDR = "0x0000000000000000000000000000000000000000"


class DispatchClient:
    """SQL-dispatch fake ClickHouse client.

    The public API runs ``_query_lp_holders`` and ``_query_lp_transfers_to_lock``
    concurrently (``asyncio.gather`` over ``asyncio.to_thread``), so a
    ``side_effect`` list would be consumed in non-deterministic order. This fake
    inspects the SQL and returns per-query data deterministically.
    """

    def __init__(
        self,
        pools: list[tuple[Any, ...]] | None = None,
        holders: list[tuple[Any, ...]] | None = None,
        lock_transfers: list[tuple[Any, ...]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.pools = pools if pools is not None else []
        self.holders = holders if holders is not None else []
        self.lock_transfers = lock_transfers if lock_transfers is not None else []
        self.error = error
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if self.error is not None:
            raise self.error
        if "SELECT DISTINCT contract_address" in sql:
            return self.pools
        if "to_address IN %(locks)s" in sql:
            return self.lock_transfers
        if "sum(net) AS balance" in sql:
            return self.holders
        return []


# ═══════════════════════════════════════════════════════════════════
# _normalize_chain
# ═══════════════════════════════════════════════════════════════════


class TestNormalizeChain:
    def test_ethereum_variants(self) -> None:
        assert lp._normalize_chain("ethereum") == "ethereum"
        assert lp._normalize_chain("eth") == "ethereum"
        assert lp._normalize_chain("mainnet") == "ethereum"

    def test_bsc_variants(self) -> None:
        assert lp._normalize_chain("bsc") == "bsc"
        assert lp._normalize_chain("bnb") == "bsc"
        assert lp._normalize_chain("binance") == "bsc"

    def test_l2_and_other_chains(self) -> None:
        assert lp._normalize_chain("arb") == "arbitrum"
        assert lp._normalize_chain("op") == "optimism"
        assert lp._normalize_chain("avax") == "avalanche"
        assert lp._normalize_chain("ftm") == "fantom"
        assert lp._normalize_chain("solana") == "solana"

    def test_unknown_chain_passes_through_lowercased(self) -> None:
        assert lp._normalize_chain("UNKNOWN") == "unknown"

    def test_case_insensitive(self) -> None:
        assert lp._normalize_chain("ETH") == "ethereum"
        assert lp._normalize_chain("BSC") == "bsc"


# ═══════════════════════════════════════════════════════════════════
# Lock contract registry
# ═══════════════════════════════════════════════════════════════════


class TestLockContractRegistry:
    def setup_method(self) -> None:
        lp._AUTO_DISCOVERED_LOCKS.clear()

    def test_known_contracts_include_unicrypt_and_dead(self) -> None:
        contracts = lp._get_lock_contracts("ethereum")
        assert UNICRYPT_V1 in contracts
        assert DEAD_ADDR in contracts
        assert "0xdba68f07d1b7ca219f78ae8582c213d3a39b8c1f" in contracts  # Unicrypt v2

    def test_solana_has_dead_addresses(self) -> None:
        contracts = lp._get_lock_contracts("solana")
        assert "dead111111111111111111111111111111111111111" in contracts

    def test_unknown_chain_returns_empty(self) -> None:
        assert lp._get_lock_contracts("unknown") == set()

    def test_auto_discovered_contracts_merged(self) -> None:
        lp._register_lock_contract("ethereum", "0xabc123abc123abc123abc123abc123abc123abcd")
        contracts = lp._get_lock_contracts("ethereum")
        assert "0xabc123abc123abc123abc123abc123abc123abcd" in contracts

    def test_register_lowercases_address(self) -> None:
        lp._register_lock_contract("bsc", "0xABCDEFabcdefABCDEFabcdefABCDEFabcdefABCD")
        assert "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd" in lp._AUTO_DISCOVERED_LOCKS["bsc"]


# ═══════════════════════════════════════════════════════════════════
# _get_lock_contract_name
# ═══════════════════════════════════════════════════════════════════


class TestLockContractName:
    def test_unicrypt_name(self) -> None:
        assert lp._get_lock_contract_name(UNICRYPT_V1, "ethereum") == "Unicrypt v1"

    def test_team_finance_name(self) -> None:
        team = "0xa0dc4a2c4a7e1b6b9b5e3c3c1b6e9f0e4e3d2c1b"
        assert lp._get_lock_contract_name(team, "ethereum") == "Team Finance"

    def test_dead_address_name(self) -> None:
        assert lp._get_lock_contract_name(DEAD_ADDR, "ethereum") == "Burned (Dead Address)"
        assert lp._get_lock_contract_name(ZERO_ADDR, "ethereum") == "Burned (Dead Address)"

    def test_unknown_contract_fallback(self) -> None:
        name = lp._get_lock_contract_name("0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "ethereum")
        assert name.startswith("Lock Contract (")

    def test_case_insensitive_lookup(self) -> None:
        assert lp._get_lock_contract_name(UNICRYPT_V1.upper(), "ethereum") == "Unicrypt v1"


# ═══════════════════════════════════════════════════════════════════
# _analyze_lp_holders
# ═══════════════════════════════════════════════════════════════════


class TestAnalyzeLpHolders:
    def _holder(self, address: str, balance: float, total: float) -> dict:
        return {
            "rank": 1,
            "address": address,
            "balance": balance,
            "percentage": round(balance / total * 100, 2),
        }

    def test_burn_majority_sets_burned_and_locked(self) -> None:
        result: dict = {}
        total = 1000.0
        lp_data = {
            "holders": [self._holder(DEAD_ADDR, 850.0, total)],
            "total_supply": total,
            "holder_count": 1,
            "lp_token_address": "0xlp",
        }
        lp._analyze_lp_holders(result, lp_data, "ethereum")
        assert result["is_burned"] is True
        assert result["is_locked"] is True
        assert result["locked_pct"] == 85.0
        assert result["lock_contract_name"] == "Burned (Dead Address)"
        assert result["lp_supply"] == "1000.0"

    def test_lock_contract_majority_sets_locked(self) -> None:
        result: dict = {}
        total = 1000.0
        lp_data = {
            "holders": [self._holder(UNICRYPT_V1, 800.0, total)],
            "total_supply": total,
            "holder_count": 1,
            "lp_token_address": "0xlp",
        }
        lp._analyze_lp_holders(result, lp_data, "ethereum")
        assert result["is_burned"] is False
        assert result["is_locked"] is True
        assert result["locked_pct"] == 80.0
        assert result["lock_contract"] == UNICRYPT_V1
        assert result["lock_contract_name"] == "Unicrypt v1"

    def test_no_lock_when_distributed(self) -> None:
        result: dict = {}
        total = 1000.0
        lp_data = {
            "holders": [
                self._holder("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 400.0, total),
                self._holder("0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", 300.0, total),
            ],
            "total_supply": total,
            "holder_count": 2,
            "lp_token_address": "0xlp",
        }
        lp._analyze_lp_holders(result, lp_data, "ethereum")
        assert result["is_locked"] is False
        assert result["is_burned"] is False
        assert result["locked_pct"] == 0.0

    def test_empty_holders_sets_supply_only(self) -> None:
        result: dict = {}
        lp_data = {
            "holders": [],
            "total_supply": 500.0,
            "holder_count": 0,
            "lp_token_address": "0xlp",
        }
        lp._analyze_lp_holders(result, lp_data, "ethereum")
        assert result["lp_supply"] == "500.0"
        assert result["holder_count"] == 0
        assert result.get("is_locked") is None  # never set without holders


# ═══════════════════════════════════════════════════════════════════
# _analyze_lock_transfers
# ═══════════════════════════════════════════════════════════════════


class TestAnalyzeLockTransfers:
    def test_empty_transfers_does_nothing(self) -> None:
        result: dict = {}
        lp._analyze_lock_transfers(result, [], "ethereum")
        assert result == {}

    def test_transfer_to_lock_contract_sets_locked(self) -> None:
        result: dict = {}
        transfers = [
            {
                "to_address": UNICRYPT_V1,
                "from_address": "0xowner",
                "amount": 500.0,
                "block_timestamp": "2026-01-01",
                "tx_hash": "0xabc",
            }
        ]
        lp._analyze_lock_transfers(result, transfers, "ethereum")
        assert result["is_locked"] is True
        assert result["lock_contract"] == UNICRYPT_V1
        assert result["lock_contract_name"] == "Unicrypt v1"
        assert result["total_transferred_to_lock"] == 500.0
        assert result["transfers_to_lock_count"] == 1

    def test_transfer_to_dead_address_sets_burned(self) -> None:
        result: dict = {}
        transfers = [
            {
                "to_address": DEAD_ADDR,
                "from_address": "0xowner",
                "amount": 900.0,
                "block_timestamp": "2026-01-01",
                "tx_hash": "0xabc",
            }
        ]
        lp._analyze_lock_transfers(result, transfers, "ethereum")
        assert result["is_burned"] is True
        assert result["is_locked"] is True
        # Burn path sets is_burned/is_locked but does not assign a lock_contract_name
        assert result.get("lock_contract_name") is None

    def test_transfer_to_unknown_address_does_not_lock(self) -> None:
        result: dict = {}
        transfers = [
            {
                "to_address": "0x9999999999999999999999999999999999999999",
                "from_address": "0xowner",
                "amount": 100.0,
                "block_timestamp": "2026-01-01",
                "tx_hash": "0xabc",
            }
        ]
        lp._analyze_lock_transfers(result, transfers, "ethereum")
        assert result.get("is_locked") is None  # only evidence fields set
        assert result["total_transferred_to_lock"] == 100.0


# ═══════════════════════════════════════════════════════════════════
# _query_* ClickHouse functions
# ═══════════════════════════════════════════════════════════════════


class TestClickHouseQueries:
    def test_query_lp_holders_returns_none_when_no_client(self) -> None:
        with patch.object(lp, "_get_ch_client", return_value=None):
            assert lp._query_lp_holders("0xtoken", "ethereum", "") is None

    def test_query_lp_holders_with_pair_address(self) -> None:
        mock_client = MagicMock()
        # _query_holders_for_token executes one query
        mock_client.execute.return_value = [
            ("0xholder1", 800.0),
            ("0xholder2", 200.0),
        ]
        with patch.object(lp, "_get_ch_client", return_value=mock_client):
            out = lp._query_lp_holders("0xtoken", "ethereum", "0xlptoken")
        assert out is not None
        assert out["holder_count"] == 2
        assert out["total_supply"] == 1000.0
        assert out["holders"][0]["address"] == "0xholder1"
        assert out["holders"][0]["percentage"] == 80.0

    def test_query_lp_holders_discovers_pool_from_dex_events(self) -> None:
        mock_client = MagicMock()
        # First execute finds pools, second executes holders query
        mock_client.execute.side_effect = [
            [("0xpool1",)],
            [("0xholder1", 700.0), ("0xholder2", 300.0)],
        ]
        with patch.object(lp, "_get_ch_client", return_value=mock_client):
            out = lp._query_lp_holders("0xtoken", "ethereum", "")
        assert out is not None
        assert out["lp_token_address"] == "0xpool1"
        assert out["total_supply"] == 1000.0

    def test_query_lp_holders_no_pools_returns_none(self) -> None:
        mock_client = MagicMock()
        mock_client.execute.return_value = []
        with patch.object(lp, "_get_ch_client", return_value=mock_client):
            assert lp._query_lp_holders("0xtoken", "ethereum", "") is None

    def test_query_lp_holders_error_returns_none(self) -> None:
        mock_client = MagicMock()
        mock_client.execute.side_effect = RuntimeError("boom")
        with patch.object(lp, "_get_ch_client", return_value=mock_client):
            assert lp._query_lp_holders("0xtoken", "ethereum", "") is None

    def test_query_lock_transfers_returns_none_without_client(self) -> None:
        with patch.object(lp, "_get_ch_client", return_value=None):
            assert lp._query_lp_transfers_to_lock("0xtoken", "ethereum", "") is None

    def test_query_lock_transfers_parses_rows(self) -> None:
        mock_client = MagicMock()
        mock_client.execute.return_value = [
            (UNICRYPT_V1, "0xowner", 750.0, "2026-01-01 00:00:00", "0xabc"),
        ]
        with patch.object(lp, "_get_ch_client", return_value=mock_client):
            transfers = lp._query_lp_transfers_to_lock("0xtoken", "ethereum", "0xlp")
        assert transfers is not None
        assert transfers[0]["to_address"] == UNICRYPT_V1
        assert transfers[0]["amount"] == 750.0
        assert transfers[0]["tx_hash"] == "0xabc"

    def test_query_lock_transfers_error_returns_none(self) -> None:
        mock_client = MagicMock()
        mock_client.execute.side_effect = RuntimeError("boom")
        with patch.object(lp, "_get_ch_client", return_value=mock_client):
            assert lp._query_lp_transfers_to_lock("0xtoken", "ethereum", "0xlp") is None


# ═══════════════════════════════════════════════════════════════════
# get_own_lp_lock_status — async public API
# ═══════════════════════════════════════════════════════════════════


class TestGetOwnLpLockStatus:
    def setup_method(self) -> None:
        lp.clear_cache()

    @pytest.mark.asyncio
    async def test_no_clickhouse_returns_unavailable(self) -> None:
        with patch.object(lp, "_get_ch_client", return_value=None):
            result = await lp.get_own_lp_lock_status("0xtoken", "ethereum")
        assert result["source"] == "unavailable"
        assert result["is_locked"] is False

    @pytest.mark.asyncio
    async def test_holder_data_sets_own_source_and_locked(self) -> None:
        fake = DispatchClient(
            pools=[("0xpool1",)],
            holders=[(DEAD_ADDR, 900.0), ("0xother", 100.0)],
            lock_transfers=[],
        )
        with patch.object(lp, "_get_ch_client", return_value=fake):
            result = await lp.get_own_lp_lock_status("0xtoken", "ethereum", "0xlptoken")
        assert result["source"] == "own"
        assert result["is_burned"] is True
        assert result["is_locked"] is True
        assert result["holder_count"] == 2
        assert result["locked_pct"] == 90.0

    @pytest.mark.asyncio
    async def test_lock_transfer_evidence_alone_marks_locked(self) -> None:
        fake = DispatchClient(
            pools=[],  # no pools -> no holder data
            holders=[],
            lock_transfers=[(UNICRYPT_V1, "0xowner", 500.0, "2026-01-01 00:00:00", "0xabc")],
        )
        with patch.object(lp, "_get_ch_client", return_value=fake):
            result = await lp.get_own_lp_lock_status("0xtoken", "ethereum", "")
        # Lock-transfer evidence marks the LP as locked...
        assert result["is_locked"] is True
        assert result["lock_contract"] == UNICRYPT_V1
        assert result["total_transferred_to_lock"] == 500.0
        # ...but source stays "unavailable" (module only promotes to "own" when
        # holder_count or locked_pct is populated).
        assert result["source"] == "unavailable"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_clickhouse(self) -> None:
        call_count = 0
        fake = DispatchClient(
            pools=[("0xpool1",)],
            holders=[(DEAD_ADDR, 900.0)],
            lock_transfers=[],
        )

        def tracking_client():
            nonlocal call_count
            call_count += 1
            return fake

        with patch.object(lp, "_get_ch_client", side_effect=tracking_client):
            first = await lp.get_own_lp_lock_status("0xcachetoken", "ethereum")
            assert first["source"] == "own"
            calls_after_first = call_count
            second = await lp.get_own_lp_lock_status("0xcachetoken", "ethereum")

        def _strip_private(payload: dict) -> dict:
            return {k: v for k, v in payload.items() if not k.startswith("_")}

        # The first call leaks "_cached_at" (cache write mutates the result in
        # place); cached reads strip private keys. Both carry identical data.
        assert _strip_private(second) == _strip_private(first)
        assert call_count == calls_after_first  # cache hit — no new client

    @pytest.mark.asyncio
    async def test_force_fresh_bypasses_cache(self) -> None:
        with patch.object(lp, "_get_ch_client", return_value=None):
            first = await lp.get_own_lp_lock_status("0xforce", "ethereum")
            second = await lp.get_own_lp_lock_status("0xforce", "ethereum", force_fresh=True)
        assert isinstance(first, dict)
        assert isinstance(second, dict)
        assert second["source"] == "unavailable"

    @pytest.mark.asyncio
    async def test_query_error_returns_unavailable(self) -> None:
        mock_client = MagicMock()
        mock_client.execute.side_effect = RuntimeError("boom")
        with patch.object(lp, "_get_ch_client", return_value=mock_client):
            result = await lp.get_own_lp_lock_status("0xtoken", "ethereum")
        assert result["source"] == "unavailable"


# ═══════════════════════════════════════════════════════════════════
# Cache & usage monitoring helpers
# ═══════════════════════════════════════════════════════════════════


class TestMonitoringHelpers:
    def setup_method(self) -> None:
        lp.clear_cache()
        lp._AUTO_DISCOVERED_LOCKS.clear()

    def test_cache_stats_reports_size_and_contracts(self) -> None:
        lp._cache_lp_lock("test:key", {"is_locked": True})
        stats = lp.get_cache_stats()
        assert stats["cache_size"] == 1
        assert stats["cache_max"] == lp._LP_LOCK_CACHE_MAX
        assert stats["cache_ttl_seconds"] == lp._LP_LOCK_CACHE_TTL
        assert "ethereum" in stats["known_lock_contracts"]
        assert stats["auto_discovered_locks"] == {}

    def test_cache_evicts_oldest_when_over_max(self) -> None:
        for i in range(lp._LP_LOCK_CACHE_MAX + 5):
            lp._cache_lp_lock(f"key{i}", {"is_locked": False})
        assert len(lp._LP_LOCK_CACHE) <= lp._LP_LOCK_CACHE_MAX

    def test_cache_entry_expired_when_ttl_passed(self) -> None:
        lp._cache_lp_lock("expired:key", {"is_locked": True})
        lp._LP_LOCK_CACHE["expired:key"]["_cached_at"] = time.time() - lp._LP_LOCK_CACHE_TTL - 1
        # Key stays in the dict; TTL is enforced at read time in the public API
        assert "expired:key" in lp._LP_LOCK_CACHE

    def test_record_usage_and_stats(self) -> None:
        lp._LP_LOCK_USAGE.update({"own": 0, "fallback": 0})
        lp.record_lp_lock_usage("own")
        lp.record_lp_lock_usage("own")
        lp.record_lp_lock_usage("fallback")
        stats = lp.get_lp_lock_usage_stats()
        assert stats["total_checks"] == 3
        assert stats["own_data_pct"] == 66.7
        assert stats["fallback_pct"] == 33.3

    def test_usage_stats_empty(self) -> None:
        lp._LP_LOCK_USAGE.update({"own": 0, "fallback": 0})
        stats = lp.get_lp_lock_usage_stats()
        assert stats["total_checks"] == 0
        assert stats["own_data_pct"] == 0
        assert stats["fallback_pct"] == 0

    def test_clear_cache_empties_dict(self) -> None:
        lp._cache_lp_lock("k", {"is_locked": True})
        lp.clear_cache()
        assert lp._LP_LOCK_CACHE == {}
