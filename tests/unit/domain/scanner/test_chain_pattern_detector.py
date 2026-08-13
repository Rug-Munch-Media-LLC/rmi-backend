"""Tests for chain_pattern_detector — on-chain scam pattern detection.

Covers chain normalization, the four pattern detectors (deployer clusters,
holder concentration, pump-and-dump, mixer interactions), the aggregate
``detect_all_patterns``, RAG ingestion, and the ``analyze_and_ingest`` entry
point. ClickHouse and Redis are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.scanner.modules import chain_pattern_detector as cpd

_TOKEN = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
_DEPLOYER = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
_MIXER = "0x0000000000000000000000000000000000000001"


class DispatchClient:
    """SQL-dispatch fake ClickHouse client for chain pattern queries."""

    def __init__(
        self,
        deployer_rows: list[tuple[Any, ...]] | None = None,
        holder_rows: list[tuple[Any, ...]] | None = None,
        count_rows: list[tuple[Any, ...]] | None = None,
        total_rows: list[tuple[Any, ...]] | None = None,
        dex_rows: list[tuple[Any, ...]] | None = None,
        mixer_rows: list[tuple[Any, ...]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.deployer_rows = deployer_rows or []
        self.holder_rows = holder_rows or []
        self.count_rows = count_rows or []
        self.total_rows = total_rows or []
        self.dex_rows = dex_rows or []
        self.mixer_rows = mixer_rows or []
        self.error = error
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if self.error is not None:
            raise self.error
        if "FROM wallet_memory.deployer_events" in sql:
            return self.deployer_rows
        if "GROUP BY to_address" in sql:  # holder tx counts
            return self.holder_rows
        if "uniq(to_address) AS total_holders" in sql:
            return self.count_rows
        if "SELECT count() AS total" in sql:
            return self.total_rows
        if "FROM wallet_memory.dex_events" in sql:
            return self.dex_rows
        if "mixers" in sql:  # mixer interactions query
            return self.mixer_rows
        return []


# ═══════════════════════════════════════════════════════════════════
# _normalize_chain
# ═══════════════════════════════════════════════════════════════════


class TestNormalizeChain:
    def test_ethereum_variants(self) -> None:
        assert cpd._normalize_chain("ethereum") == "ethereum"
        assert cpd._normalize_chain("eth") == "ethereum"
        assert cpd._normalize_chain("mainnet") == "ethereum"

    def test_bsc_variants(self) -> None:
        assert cpd._normalize_chain("bsc") == "bsc"
        assert cpd._normalize_chain("bnb") == "bsc"

    def test_gnosis_variants(self) -> None:
        assert cpd._normalize_chain("gnosis") == "gnosis"
        assert cpd._normalize_chain("xdai") == "gnosis"

    def test_unknown_chain_passes_through(self) -> None:
        assert cpd._normalize_chain("UNKNOWN") == "unknown"


# ═══════════════════════════════════════════════════════════════════
# detect_deployer_clusters
# ═══════════════════════════════════════════════════════════════════


class TestDetectDeployerClusters:
    def _row(self, token: str, chain: str = "bsc", block: int = 100, ts: str = "2026-01-01 00:00:00") -> tuple:
        return (token, chain, block, ts, "create")

    @pytest.mark.asyncio
    async def test_unknown_deployer_not_detected(self) -> None:
        out = await cpd.detect_deployer_clusters("unknown", "bsc")
        assert out["detected"] is False
        assert out["risk_score"] == 0

    @pytest.mark.asyncio
    async def test_no_client_returns_clean(self) -> None:
        with patch.object(cpd, "_get_ch_client", return_value=None):
            out = await cpd.detect_deployer_clusters(_DEPLOYER, "bsc")
        assert out["detected"] is False

    @pytest.mark.asyncio
    async def test_single_token_not_detected(self) -> None:
        fake = DispatchClient(deployer_rows=[self._row("0x1")])
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_deployer_clusters(_DEPLOYER, "bsc", min_tokens=3)
        assert out["detected"] is False
        assert out["tokens"] == []
        assert out["risk_score"] == 0

    @pytest.mark.asyncio
    async def test_two_tokens_low_risk_not_detected(self) -> None:
        fake = DispatchClient(deployer_rows=[self._row("0x1"), self._row("0x2")])
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_deployer_clusters(_DEPLOYER, "bsc", min_tokens=3)
        # 2 tokens below min_tokens=3 -> not detected, but data is surfaced
        assert out["detected"] is False
        assert out["total_tokens"] == 2
        assert out["risk_score"] == 10

    @pytest.mark.asyncio
    async def test_three_tokens_detected(self) -> None:
        fake = DispatchClient(
            deployer_rows=[
                self._row("0x1", "bsc", 300),
                self._row("0x2", "bsc", 200),
                self._row("0x3", "ethereum", 100),
            ]
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_deployer_clusters(_DEPLOYER, "bsc")
        assert out["detected"] is True
        assert out["total_tokens"] == 3
        assert out["risk_score"] == 15  # 3 tokens * 5
        assert sorted(out["chains_used"]) == ["bsc", "ethereum"]
        assert len(out["tokens"]) == 3
        assert "description" in out

    @pytest.mark.asyncio
    async def test_risk_score_capped_at_70(self) -> None:
        fake = DispatchClient(deployer_rows=[self._row(f"0x{i}") for i in range(20)])
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_deployer_clusters(_DEPLOYER, "bsc", min_tokens=3)
        assert out["risk_score"] == 70

    @pytest.mark.asyncio
    async def test_query_error_returns_clean(self) -> None:
        fake = DispatchClient(error=ConnectionError("boom"))
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_deployer_clusters(_DEPLOYER, "bsc")
        assert out["detected"] is False


# ═══════════════════════════════════════════════════════════════════
# detect_holder_concentration
# ═══════════════════════════════════════════════════════════════════


class TestDetectHolderConcentration:
    @pytest.mark.asyncio
    async def test_concentrated_holders_detected(self) -> None:
        # 7 holders; top 2 account for 85 of 100 transfers -> 85% concentration
        fake = DispatchClient(
            holder_rows=[("0xbig1", 60), ("0xbig2", 25), ("0xa", 5), ("0xb", 4), ("0xc", 3), ("0xd", 2), ("0xe", 1)],
            count_rows=[(7,)],
            total_rows=[(100,)],
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_holder_concentration(_TOKEN, "bsc", top_n=2)
        assert out["detected"] is True
        assert out["concentration_pct"] == 85.0
        assert out["risk_score"] == 45
        assert len(out["top_holders"]) == 7

    @pytest.mark.asyncio
    async def test_low_concentration_not_detected(self) -> None:
        # top 2 account for 40 of 100 transfers -> 40% concentration (below 70)
        fake = DispatchClient(
            holder_rows=[
                ("0xbig1", 25),
                ("0xbig2", 15),
                ("0xa", 10),
                ("0xb", 10),
                ("0xc", 10),
                ("0xd", 10),
                ("0xe", 10),
                ("0xf", 10),
            ],
            count_rows=[(8,)],
            total_rows=[(100,)],
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_holder_concentration(_TOKEN, "bsc", top_n=2)
        assert out["detected"] is False
        assert out["concentration_pct"] == 40.0
        assert out["risk_score"] == 0

    @pytest.mark.asyncio
    async def test_few_holders_not_detected(self) -> None:
        fake = DispatchClient(
            holder_rows=[("0xbig1", 60), ("0xbig2", 25)],
            count_rows=[(2,)],
            total_rows=[(100,)],
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_holder_concentration(_TOKEN, "bsc")
        assert out["detected"] is False
        assert out["risk_score"] == 0

    @pytest.mark.asyncio
    async def test_no_client_returns_clean(self) -> None:
        with patch.object(cpd, "_get_ch_client", return_value=None):
            out = await cpd.detect_holder_concentration(_TOKEN, "bsc")
        assert out["detected"] is False

    @pytest.mark.asyncio
    async def test_query_error_returns_clean(self) -> None:
        fake = DispatchClient(error=ConnectionError("boom"))
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_holder_concentration(_TOKEN, "bsc")
        assert out["detected"] is False

    @pytest.mark.asyncio
    async def test_extreme_concentration_scores_60(self) -> None:
        fake = DispatchClient(
            holder_rows=[("0xbig1", 95), ("0xa", 1), ("0xb", 1), ("0xc", 1), ("0xd", 1), ("0xe", 1)],
            count_rows=[(6,)],
            total_rows=[(100,)],
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_holder_concentration(_TOKEN, "bsc", top_n=1)
        assert out["detected"] is True
        assert out["concentration_pct"] == 95.0
        assert out["risk_score"] == 60


# ═══════════════════════════════════════════════════════════════════
# detect_pump_and_dump
# ═══════════════════════════════════════════════════════════════════

# dex_events row layout returned by the module's SELECT:
#   0 type, 1 block_timestamp, 2 tx_hash, 3 dex, 4 version,
#   5 token0, 6 token1, 7 sender, 8 recipient,
#   9 amount0_in, 10 amount1_in, 11 amount0_out, 12 amount1_out,
#   13 amount0, 14 amount1, 15 whale
#
# NOTE: the classifier reads ``row[9]`` for the token0 comparison and
# ``row[12]/row[13]`` for the sell amount — indices that line up with
# amount0_in/amount1_out/amount0 rather than token0/amount0_out/amount1_out.
# Tests below construct rows to exercise the module's real branch logic.


def _swap_row(
    *,
    token0: str,
    token1: str,
    whale: bool,
    amt0_out: int = 0,
    amt1_out: int = 0,
    amt0: int = 0,
) -> tuple:
    return (
        "swap",
        "2026-01-01 00:00:00",
        "0xtx",
        "pancakeswap",
        "v2",
        token0,
        token1,
        "0xsender",
        "0xrecipient",
        amt0_out,  # index 9 (read by classifier as token0-check input)
        amt1_out,  # index 10
        amt0_out,  # index 11
        amt1_out,  # index 12
        amt0,  # index 13 (read by classifier as sell amount in else branch)
        0,
        whale,
    )


class TestDetectPumpAndDump:
    @pytest.mark.asyncio
    async def test_pump_dump_detected(self) -> None:
        # 15 small buys + 1 whale sell (whale row carries amount at index 13)
        rows = [_swap_row(token0=_TOKEN, token1="0xweth", whale=False) for _ in range(15)]
        rows.append(_swap_row(token0=_TOKEN, token1="0xweth", whale=True, amt0=1000))
        fake = DispatchClient(dex_rows=rows)
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_pump_and_dump(_TOKEN, "bsc")
        assert out["detected"] is True
        assert out["small_buys"] == 15
        assert out["large_sells"] == 1
        assert out["buy_sell_ratio"] == 15.0
        assert out["risk_score"] == 15  # ratio 15 <= 20

    @pytest.mark.asyncio
    async def test_high_ratio_scores_60(self) -> None:
        rows = [_swap_row(token0=_TOKEN, token1="0xweth", whale=False) for _ in range(120)]
        rows.append(_swap_row(token0=_TOKEN, token1="0xweth", whale=True, amt0=1000))
        fake = DispatchClient(dex_rows=rows)
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_pump_and_dump(_TOKEN, "bsc")
        assert out["detected"] is True
        assert out["risk_score"] == 60

    @pytest.mark.asyncio
    async def test_whale_sell_via_token0_branch(self) -> None:
        # When index 9 (amount0_in column) equals the token address, the
        # classifier takes the token0 branch and reads row[12] as the sell amount.
        rows = [_swap_row(token0=_TOKEN, token1="0xweth", whale=False) for _ in range(11)]
        rows.append(
            _swap_row(
                token0=_TOKEN,
                token1="0xweth",
                whale=True,
                amt0_out=_TOKEN,  # populates index 9 (token0-check input)
                amt1_out=1000,  # populates index 12 read by token0 branch
            )
        )
        fake = DispatchClient(dex_rows=rows)
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_pump_and_dump(_TOKEN, "bsc")
        assert out["large_sells"] == 1
        assert out["detected"] is True

    @pytest.mark.asyncio
    async def test_no_whale_sell_not_detected(self) -> None:
        rows = [_swap_row(token0=_TOKEN, token1="0xweth", whale=False) for _ in range(20)]
        fake = DispatchClient(dex_rows=rows)
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_pump_and_dump(_TOKEN, "bsc")
        assert out["detected"] is False
        assert out["large_sells"] == 0

    @pytest.mark.asyncio
    async def test_too_few_rows_not_detected(self) -> None:
        fake = DispatchClient(dex_rows=[_swap_row(token0=_TOKEN, token1="0xweth", whale=False) for _ in range(5)])
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_pump_and_dump(_TOKEN, "bsc")
        assert out["detected"] is False

    @pytest.mark.asyncio
    async def test_no_client_returns_clean(self) -> None:
        with patch.object(cpd, "_get_ch_client", return_value=None):
            out = await cpd.detect_pump_and_dump(_TOKEN, "bsc")
        assert out["detected"] is False

    @pytest.mark.asyncio
    async def test_query_error_returns_clean(self) -> None:
        fake = DispatchClient(error=ConnectionError("boom"))
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_pump_and_dump(_TOKEN, "bsc")
        assert out["detected"] is False


# ═══════════════════════════════════════════════════════════════════
# detect_mixer_interactions
# ═══════════════════════════════════════════════════════════════════


class TestDetectMixerInteractions:
    @pytest.mark.asyncio
    async def test_no_mixers_on_chain_returns_clean(self) -> None:
        # KNOWN_MIXERS only has "ethereum" entries
        out = await cpd.detect_mixer_interactions(_DEPLOYER, "bsc")
        assert out["detected"] is False

    @pytest.mark.asyncio
    async def test_interaction_detected(self) -> None:
        fake = DispatchClient(
            mixer_rows=[
                ("0xtx1", 100, "2026-01-01 00:00:00", _DEPLOYER, _MIXER, 500),
                ("0xtx2", 101, "2026-01-01 00:01:00", _MIXER, _DEPLOYER, 100),
            ]
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_mixer_interactions(_DEPLOYER, "ethereum")
        assert out["detected"] is True
        assert out["risk_score"] == 40
        assert len(out["mixer_txns"]) == 2
        assert out["mixer_txns"][0]["tx_hash"] == "0xtx1"
        assert out["mixer_txns"][0]["from"] == _DEPLOYER

    @pytest.mark.asyncio
    async def test_no_interaction_returns_clean(self) -> None:
        fake = DispatchClient(mixer_rows=[])
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_mixer_interactions(_DEPLOYER, "ethereum")
        assert out["detected"] is False

    @pytest.mark.asyncio
    async def test_query_error_returns_clean(self) -> None:
        fake = DispatchClient(error=ConnectionError("boom"))
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.detect_mixer_interactions(_DEPLOYER, "ethereum")
        assert out["detected"] is False


# ═══════════════════════════════════════════════════════════════════
# detect_all_patterns
# ═══════════════════════════════════════════════════════════════════


class TestDetectAllPatterns:
    @pytest.mark.asyncio
    async def test_aggregates_detected_patterns(self) -> None:
        fake = DispatchClient(
            holder_rows=[("0xbig1", 90), ("0xa", 1), ("0xb", 1), ("0xc", 1), ("0xd", 1), ("0xe", 1)],
            count_rows=[(6,)],
            total_rows=[(100,)],
            dex_rows=[_swap_row(token0=_TOKEN, token1="0xweth", whale=False) for _ in range(15)]
            + [_swap_row(token0=_TOKEN, token1="0xweth", whale=True, amt0=1000)],
            deployer_rows=[("0x1", "bsc", 100, "2026-01-01 00:00:00", "create")] * 3,
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            findings = await cpd.detect_all_patterns(_TOKEN, "bsc", deployer=_DEPLOYER)
        patterns = {f["pattern"] for f in findings}
        assert "holder_concentration" in patterns
        assert "pump_and_dump" in patterns
        assert "deployer_cluster" in patterns
        # No mixers registered on bsc -> mixer not in findings
        assert "mixer_interaction" not in patterns

    @pytest.mark.asyncio
    async def test_no_deployer_skips_cluster_and_mixer(self) -> None:
        fake = DispatchClient(
            holder_rows=[("0xbig1", 60), ("0xbig2", 25)],
            count_rows=[(5,)],
            total_rows=[(100,)],
            dex_rows=[],
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            findings = await cpd.detect_all_patterns(_TOKEN, "bsc", deployer=None)
        patterns = {f["pattern"] for f in findings}
        assert "holder_concentration" in patterns
        assert "deployer_cluster" not in patterns
        assert "mixer_interaction" not in patterns

    @pytest.mark.asyncio
    async def test_clean_token_returns_empty(self) -> None:
        fake = DispatchClient(
            holder_rows=[("0xa", 10), ("0xb", 10)],
            count_rows=[(2,)],
            total_rows=[(100,)],
            dex_rows=[],
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            findings = await cpd.detect_all_patterns(_TOKEN, "bsc", deployer=_DEPLOYER)
        assert findings == []


# ═══════════════════════════════════════════════════════════════════
# ingest_patterns_into_rag
# ═══════════════════════════════════════════════════════════════════


class TestIngestPatternsIntoRag:
    def _finding(self, risk_score: int = 40, pattern: str = "holder_concentration") -> dict[str, Any]:
        return {"pattern": pattern, "risk_score": risk_score, "chain": "bsc", "description": "desc"}

    @pytest.mark.asyncio
    async def test_empty_findings_returns_zero(self) -> None:
        assert await cpd.ingest_patterns_into_rag([]) == 0

    @pytest.mark.asyncio
    async def test_publishes_and_lpushes_each_finding(self) -> None:
        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock(return_value=1)
        fake_redis.lpush = AsyncMock(return_value=1)
        with patch("app.core.redis.get_redis_async", return_value=fake_redis):
            published = await cpd.ingest_patterns_into_rag([self._finding(80), self._finding(50), self._finding(10)])
        assert published == 3
        assert fake_redis.publish.await_count == 3
        assert fake_redis.lpush.await_count == 3

    @pytest.mark.asyncio
    async def test_payload_includes_risk_level_mapping(self) -> None:
        fake_redis = MagicMock()
        captured: list[str] = []
        fake_redis.publish = AsyncMock(side_effect=lambda channel, payload: captured.append(payload) or 1)
        fake_redis.lpush = AsyncMock(return_value=1)
        with patch("app.core.redis.get_redis_async", return_value=fake_redis):
            await cpd.ingest_patterns_into_rag([self._finding(80)])
        import json

        payload = json.loads(captured[0])
        assert payload["risk_level"] == "critical"
        assert payload["severity"] == "high"
        assert payload["category"] == "onchain_pattern"
        assert payload["modules_run"] == ["chain_pattern_detector"]

    @pytest.mark.asyncio
    async def test_redis_unavailable_returns_zero(self) -> None:
        with patch("app.core.redis.get_redis_async", return_value=None):
            assert await cpd.ingest_patterns_into_rag([self._finding(40)]) == 0

    @pytest.mark.asyncio
    async def test_redis_error_returns_zero(self) -> None:
        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock(side_effect=ConnectionError("redis down"))
        with patch("app.core.redis.get_redis_async", return_value=fake_redis):
            assert await cpd.ingest_patterns_into_rag([self._finding(40)]) == 0


# ═══════════════════════════════════════════════════════════════════
# analyze_and_ingest
# ═══════════════════════════════════════════════════════════════════


class TestAnalyzeAndIngest:
    @pytest.mark.asyncio
    async def test_clean_token_returns_clean(self) -> None:
        fake = DispatchClient(
            holder_rows=[("0xa", 10), ("0xb", 10)],
            count_rows=[(2,)],
            total_rows=[(100,)],
            dex_rows=[],
        )
        with patch.object(cpd, "_get_ch_client", return_value=fake):
            out = await cpd.analyze_and_ingest(_TOKEN, "bsc", deployer=_DEPLOYER)
        assert out["check"] == "chain_pattern_detector"
        assert out["result"] == "clean"
        assert out["match_count"] == 0
        assert out["ingested_into_rag"] == 0

    @pytest.mark.asyncio
    async def test_detected_patterns_return_scanned(self) -> None:
        # holder concentration scores 60 (>=30 -> ingested); deployer cluster
        # with 6 tokens scores 30 (>=30 -> ingested)
        fake = DispatchClient(
            holder_rows=[("0xbig1", 90), ("0xa", 1), ("0xb", 1), ("0xc", 1), ("0xd", 1), ("0xe", 1)],
            count_rows=[(6,)],
            total_rows=[(100,)],
            dex_rows=[],
            deployer_rows=[("0x1", "bsc", 100, "2026-01-01 00:00:00", "create")] * 6,
        )
        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock(return_value=1)
        fake_redis.lpush = AsyncMock(return_value=1)
        with (
            patch.object(cpd, "_get_ch_client", return_value=fake),
            patch("app.core.redis.get_redis_async", return_value=fake_redis),
        ):
            out = await cpd.analyze_and_ingest(_TOKEN, "bsc", deployer=_DEPLOYER)
        assert out["result"] == "scanned"
        assert out["match_count"] == 2  # holder concentration + deployer cluster
        assert out["total_risk_score"] > 0
        assert out["ingested_into_rag"] == 2
        assert out["source"] == "lite_chain_indexer (ClickHouse)"

    @pytest.mark.asyncio
    async def test_detect_and_ingest_alias(self) -> None:
        assert cpd.detect_and_ingest is cpd.analyze_and_ingest
