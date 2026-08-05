"""Tests for cross_launch_correlation — repeat-bundler wallet correlation.

Pure-logic tests for the JSON-backed store, scoring thresholds, and the
ClickHouse hydration query path (fake client, no live service).

Covered:
  - record_launch — non-wallet filtering, early-window enforcement, dedupe
  - get_wallet_stats / _aggregate — launch count, avg offset, chains, first/last
  - score_wallet — 3+/5+/10+ cumulative bands, same-chain bonus, cap
  - cross_reference — clean vs flagged, same_chain_pattern, token score
  - persistence round-trip (atomic JSON store)
  - analyze_cross_launch / compute_cross_launch_score (async public API)
  - hydrate_from_clickhouse — graceful unavailable + fake-client success path
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.scanners.engines import cross_launch_correlation as clc
from app.domains.scanners.engines.cross_launch_correlation import (
    MAX_SCORE,
    SCORE_HIGH,
    SCORE_SAME_CHAIN_BONUS,
    SCORE_SUSPICIOUS,
    CrossLaunchCorrelator,
    WalletStats,
)

WALLET_A = "walletA11111111111111111111111111111111"  # 32-char placeholder (solana-style)
WALLET_B = "walletB22222222222222222222222222222222"
EVM_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ZERO = "0x0000000000000000000000000000000000000000"


def buyer(addr: str, block: int) -> dict[str, Any]:
    return {"address": addr, "first_buy_block": block}


def _correlator(tmp_path: Any) -> CrossLaunchCorrelator:
    return CrossLaunchCorrelator(store_path=tmp_path / "wallet_correlation.json")


# ═══════════════════════════════════════════════════════════════════════════
# record_launch
# ═══════════════════════════════════════════════════════════════════════════


class TestRecordLaunch:
    def test_skips_empty_and_zero_addresses(self, tmp_path: Any) -> None:
        c = _correlator(tmp_path)
        n = c.record_launch(
            "solana",
            "tok1",
            deploy_block=100,
            early_buyers=[buyer(WALLET_A, 102), buyer("", 101), buyer(ZERO, 103)],
        )
        assert n == 1
        assert len(c) == 1

    def test_requires_known_deploy_block(self, tmp_path: Any) -> None:
        c = _correlator(tmp_path)
        assert c.record_launch("solana", "tok1", 0, [buyer(WALLET_A, 102)]) == 0
        assert len(c) == 0

    def test_enforces_early_buy_window(self, tmp_path: Any) -> None:
        c = _correlator(tmp_path)
        # block 120 is 20 blocks after deploy -> outside the 10-block window
        n = c.record_launch("solana", "tok1", 100, [buyer(WALLET_A, 120)])
        assert n == 0

    def test_dedupes_same_launch(self, tmp_path: Any) -> None:
        c = _correlator(tmp_path)
        c.record_launch("solana", "tok1", 100, [buyer(WALLET_A, 102)])
        c.record_launch("solana", "tok1", 100, [buyer(WALLET_A, 102)])  # re-scan
        stats = c.get_wallet_stats(WALLET_A)
        assert stats is not None
        assert stats.token_launches_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# aggregation
# ═══════════════════════════════════════════════════════════════════════════


class TestAggregation:
    def test_wallet_stats_aggregation(self, tmp_path: Any) -> None:
        c = _correlator(tmp_path)
        c.record_launch("solana", "tok1", 100, [buyer(WALLET_A, 102)], timestamp=1000.0)
        c.record_launch("solana", "tok2", 200, [buyer(WALLET_A, 205)], timestamp=2000.0)
        c.record_launch("ethereum", "tok3", 300, [buyer(WALLET_A, 301)], timestamp=3000.0)

        stats = c.get_wallet_stats(WALLET_A)
        assert stats is not None
        assert stats.token_launches_count == 3
        assert stats.avg_buy_block_offset == pytest.approx((2 + 5 + 1) / 3)
        assert stats.chains == ["ethereum", "solana"]
        assert stats.first_seen == 1000.0
        assert stats.last_seen == 3000.0

    def test_normalizes_evm_addresses(self, tmp_path: Any) -> None:
        c = _correlator(tmp_path)
        c.record_launch("ethereum", "tok1", 100, [buyer(EVM_A.upper(), 102)])
        assert c.get_wallet_stats(EVM_A) is not None
        assert c.get_wallet_stats(EVM_A.upper()) is not None  # same normalized key


# ═══════════════════════════════════════════════════════════════════════════
# scoring
# ═══════════════════════════════════════════════════════════════════════════


class TestScoreWallet:
    @staticmethod
    def _stats(launches: int, chains: list[str]) -> WalletStats:
        return WalletStats("w", launches, 0.0, 0.0, 1.0, chains)

    def test_below_threshold_no_score(self) -> None:
        assert CrossLaunchCorrelator.score_wallet(self._stats(2, ["solana"])) == (0, "NONE", [])

    def test_three_plus_suspicious_same_chain_bonus(self) -> None:
        score, level, flags = CrossLaunchCorrelator.score_wallet(self._stats(3, ["solana"]))
        assert score == SCORE_SUSPICIOUS + SCORE_SAME_CHAIN_BONUS
        assert level == "SUSPICIOUS"
        assert flags == ["CROSS_LAUNCH_REPEAT_3PLUS", "CROSS_LAUNCH_SAME_CHAIN"]

    def test_five_plus_high_cumulative(self) -> None:
        score, level, _ = CrossLaunchCorrelator.score_wallet(self._stats(5, ["solana"]))
        assert score == SCORE_SUSPICIOUS + SCORE_HIGH + SCORE_SAME_CHAIN_BONUS
        assert level == "HIGH"

    def test_ten_plus_critical_capped(self) -> None:
        score, level, flags = CrossLaunchCorrelator.score_wallet(self._stats(10, ["solana", "ethereum"]))
        assert score == MAX_SCORE  # 20+35+50=105 -> capped at 100
        assert level == "CRITICAL"
        assert flags == ["CROSS_LAUNCH_REPEAT_10PLUS"]  # no same-chain flag (multi-chain)

    def test_multi_chain_gets_no_same_chain_bonus(self) -> None:
        score, _, _ = CrossLaunchCorrelator.score_wallet(self._stats(5, ["solana", "ethereum"]))
        assert score == SCORE_SUSPICIOUS + SCORE_HIGH  # no +10


# ═══════════════════════════════════════════════════════════════════════════
# cross-reference
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossReference:
    def test_clean_when_below_threshold(self, tmp_path: Any) -> None:
        c = _correlator(tmp_path)
        c.record_launch("solana", "tok1", 100, [buyer(WALLET_A, 102)])
        c.record_launch("solana", "tok2", 200, [buyer(WALLET_A, 202)])
        ref = c.cross_reference("solana", [buyer(WALLET_A, 300)])
        assert ref["result"] == "clean"
        assert ref["repeat_count"] == 0
        assert ref["flags"] == []

    def test_flagged_with_repeat_wallets(self, tmp_path: Any) -> None:
        c = _correlator(tmp_path)
        for i, blk in enumerate((100, 200, 300)):
            c.record_launch("solana", f"tok{i}", blk, [buyer(WALLET_A, blk + 2), buyer(WALLET_B, blk + 1)])

        ref = c.cross_reference("solana", [buyer(WALLET_A, 400), buyer(WALLET_B, 400)])
        assert ref["result"] == "flagged"
        assert ref["repeat_count"] == 2
        assert ref["max_wallet_launches"] == 3
        assert ref["same_chain_pattern"] is True
        # max wallet score 30 (3+ chain) + 5 per additional repeat wallet
        assert ref["score"] == SCORE_SUSPICIOUS + SCORE_SAME_CHAIN_BONUS + 5
        assert ref["flags"] == ["CROSS_LAUNCH_REPEAT", "CROSS_LAUNCH_SAME_CHAIN_PATTERN"]
        assert {w["wallet_address"] for w in ref["matched_wallets"]} == {WALLET_A, WALLET_B}


# ═══════════════════════════════════════════════════════════════════════════
# persistence
# ═══════════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_round_trip(self, tmp_path: Any) -> None:
        path = tmp_path / "wallet_correlation.json"
        c = CrossLaunchCorrelator(store_path=path)
        c.record_launch("solana", "tok1", 100, [buyer(WALLET_A, 102)])
        c.persist()

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["version"] == 1
        assert WALLET_A in raw["wallets"]

        c2 = CrossLaunchCorrelator(store_path=path)
        c2.load()
        stats = c2.get_wallet_stats(WALLET_A)
        assert stats is not None
        assert stats.token_launches_count == 1
        assert stats.chains == ["solana"]

    def test_load_missing_file_is_empty(self, tmp_path: Any) -> None:
        c = CrossLaunchCorrelator(store_path=tmp_path / "nope.json")
        c.load()
        assert len(c) == 0


# ═══════════════════════════════════════════════════════════════════════════
# async public API
# ═══════════════════════════════════════════════════════════════════════════


class TestAsyncPublicApi:
    async def test_analyze_cross_launch_records_and_flags(self, tmp_path: Any, monkeypatch: Any) -> None:
        corr = _correlator(tmp_path)
        monkeypatch.setattr(clc, "get_correlator", lambda: corr)

        for blk, tok in ((100, "tok1"), (200, "tok2"), (300, "tok3")):
            n = await clc.analyze_cross_launch(
                [buyer(WALLET_A, blk + 2)], "solana", deploy_block=blk, token_address=tok, record=True
            )
            assert n["recorded_wallets"] == 1

        flags = await clc.analyze_cross_launch(
            [buyer(WALLET_A, 402)], "solana", deploy_block=400, token_address="tok4", record=True
        )
        assert flags["result"] == "flagged"
        assert flags["repeat_count"] == 1
        assert flags["recorded_wallets"] == 1  # current launch recorded too

    async def test_compute_cross_launch_score(self, tmp_path: Any, monkeypatch: Any) -> None:
        corr = _correlator(tmp_path)
        monkeypatch.setattr(clc, "get_correlator", lambda: corr)
        for i, blk in enumerate((100, 200, 300)):
            corr.record_launch("solana", f"tok{i}", blk, [buyer(WALLET_A, blk + 2)])

        score = await clc.compute_cross_launch_score([buyer(WALLET_A, 500)], "solana")
        assert score == SCORE_SUSPICIOUS + SCORE_SAME_CHAIN_BONUS

    async def test_analyze_never_raises_on_store_error(self, tmp_path: Any, monkeypatch: Any) -> None:
        corr = _correlator(tmp_path)
        monkeypatch.setattr(clc, "get_correlator", lambda: corr)

        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("disk full")

        monkeypatch.setattr(corr, "cross_reference", boom)
        flags = await clc.analyze_cross_launch([buyer(WALLET_A, 100)], "solana", record=False)
        assert flags["result"] == "error"


# ═══════════════════════════════════════════════════════════════════════════
# ClickHouse hydration
# ═══════════════════════════════════════════════════════════════════════════


class _FakeCHClient:
    """Minimal clickhouse_driver stand-in for the hydration query path.

    The transfers query is token-aware: returns rows only for the token in
    ``params`` (matching the real ClickHouse WHERE token_address = ...).
    """

    def __init__(self, launches: list[tuple[Any, ...]], transfers: dict[str, list[tuple[Any, ...]]]) -> None:
        self._launches = launches
        self._transfers = transfers

    def execute(self, query: str, params: Any = None, settings: Any = None) -> list[tuple[Any, ...]]:
        if "deployer_events" in query:
            return self._launches
        token = (params or {}).get("token", "")
        return self._transfers.get(token, [])


class TestHydration:
    def test_unavailable_when_no_client(self, tmp_path: Any, monkeypatch: Any) -> None:
        corr = _correlator(tmp_path)
        monkeypatch.setattr(clc, "_get_ch_client", lambda: None)
        result = corr.hydrate_from_clickhouse_sync()
        assert result["result"] == "unavailable"
        assert result["launches_hydrated"] == 0

    def test_records_early_buyers_from_fake_client(self, tmp_path: Any, monkeypatch: Any) -> None:
        corr = _correlator(tmp_path)
        fake = _FakeCHClient(
            launches=[("tok1", "ethereum", 100), ("tok2", "solana", 200)],
            transfers={"tok1": [(EVM_A, 102)]},
        )
        monkeypatch.setattr(clc, "_get_ch_client", lambda: fake)
        result = corr.hydrate_from_clickhouse_sync(max_tokens=5)
        assert result["result"] == "ok"
        assert result["launches_hydrated"] == 1  # tok2 had no transfers
        assert result["wallets_recorded"] == 1
        assert corr.get_wallet_stats(EVM_A) is not None
        assert corr.get_wallet_stats(EVM_A).token_launches_count == 1  # type: ignore[union-attr]
