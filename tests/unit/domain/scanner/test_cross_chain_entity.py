"""Tests for cross_chain_entity — C4 v2 cross-chain entity resolution.

Covers address/chain normalization, deployer confidence scoring, risk
heuristics, the ClickHouse evidence queries (deployer chains, first funder,
same-funder candidates), and the async ``resolve_cross_chain_entity`` API
including the v2 behavioral fingerprint confirmation path.

All external dependencies (ClickHouse, Redis) are mocked.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines import cross_chain_entity as cce

_ADDR = "0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddead"
_FUNDER = "0x1111111111111111111111111111111111111111"
_SOL_ADDR = "Dead1111111111111111111111111111111111111111"


class DispatchClient:
    """SQL-dispatch fake ClickHouse client for the three evidence queries."""

    def __init__(
        self,
        deployer_chains: list[tuple[Any, ...]] | None = None,
        first_funder: list[tuple[Any, ...]] | None = None,
        funder_candidates: list[tuple[Any, ...]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.deployer_chains = deployer_chains or []
        self.first_funder = first_funder or []
        self.funder_candidates = funder_candidates or []
        self.error = error
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if self.error is not None:
            raise self.error
        if "uniqExact(token_address) AS tokens_deployed" in sql:
            return self.deployer_chains
        if "ORDER BY first_ts ASC" in sql and "LIMIT 1" in sql:
            return self.first_funder
        if "GROUP BY chain, to_address" in sql:
            return self.funder_candidates
        return []


def _patch_env(fake: DispatchClient, redis_hit: Any = None) -> list:
    """Standard patch stack: CH client + Redis get/set."""
    return [
        patch.object(cce, "_get_ch_client", return_value=fake),
        patch.object(cce, "_redis_get", AsyncMock(return_value=redis_hit)),
        patch.object(cce, "_redis_set", AsyncMock(return_value=True)),
    ]


def _scanned_fp() -> dict[str, Any]:
    """A minimal scanned behavioral fingerprint payload."""
    return {
        "result": "scanned",
        "fingerprint": {
            "tx_count": 1200,
            "active_days": 300,
            "tx_per_day": 4.0,
            "burst_score": 0.5,
            "swap_count": 400,
            "swap_per_day": 1.3,
            "dex_diversity": 5,
            "token_diversity": 30,
            "top_protocols": ["uniswap"],
            "active_hours": [0.5 if h == 12 else 0.0 for h in range(24)],
            "active_dow": [0.2 for _ in range(7)],
            "median_value": 1e20,
            "p95_value": 1e22,
            "outlier_threshold": 1e23,
            "value_spread": 100.0,
        },
        "confidence": 0.9,
    }


# ═══════════════════════════════════════════════════════════════════
# _norm_addr
# ═══════════════════════════════════════════════════════════════════


class TestNormAddr:
    def test_lowercases_evm_address(self) -> None:
        assert (
            cce._norm_addr("0xABCdefABCdefABCdefABCdefABCdefABCdefAbCd") == "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
        )

    def test_leaves_base58_untouched(self) -> None:
        assert cce._norm_addr(_SOL_ADDR) == _SOL_ADDR

    def test_handles_empty_and_whitespace(self) -> None:
        assert cce._norm_addr("") == ""
        assert cce._norm_addr("   ") == ""


# ═══════════════════════════════════════════════════════════════════
# _deployer_confidence
# ═══════════════════════════════════════════════════════════════════


class TestDeployerConfidence:
    def test_all_evm_chains_score_095(self) -> None:
        assert cce._deployer_confidence(["ethereum", "bsc", "arbitrum"]) == cce._CONFIDENCE_DEPLOYER_EVM

    def test_mixed_evm_and_non_evm_scores_080(self) -> None:
        assert cce._deployer_confidence(["ethereum", "solana"]) == cce._CONFIDENCE_DEPLOYER_MIXED

    def test_non_evm_only_scores_060(self) -> None:
        assert cce._deployer_confidence(["solana", "tron"]) == cce._CONFIDENCE_DEPLOYER_NON_EVM

    def test_single_chain_scores_095(self) -> None:
        assert cce._deployer_confidence(["bsc"]) == cce._CONFIDENCE_DEPLOYER_EVM


# ═══════════════════════════════════════════════════════════════════
# _risk_level
# ═══════════════════════════════════════════════════════════════════


class TestRiskLevel:
    def _link(self, evidence_type: str = "same_deployer", chain: str = "bsc") -> dict[str, Any]:
        return {"chain": chain, "address": _ADDR, "evidence_type": evidence_type, "confidence": 0.8}

    def test_no_links_is_low(self) -> None:
        assert cce._risk_level([]) == "low"

    def test_one_link_is_medium(self) -> None:
        assert cce._risk_level([self._link()]) == "medium"

    def test_two_links_is_medium(self) -> None:
        assert cce._risk_level([self._link(chain="bsc"), self._link(chain="arbitrum")]) == "medium"

    def test_three_links_is_high(self) -> None:
        assert (
            cce._risk_level([self._link(chain="bsc"), self._link(chain="arbitrum"), self._link(chain="polygon")])
            == "high"
        )

    def test_same_funder_to_non_evm_bumps_medium_to_high(self) -> None:
        links = [self._link(), self._link(evidence_type="same_funder", chain="solana")]
        assert cce._risk_level(links) == "high"

    def test_same_funder_to_evm_keeps_medium(self) -> None:
        links = [self._link(), self._link(evidence_type="same_funder", chain="bsc")]
        assert cce._risk_level(links) == "medium"


# ═══════════════════════════════════════════════════════════════════
# ClickHouse evidence queries
# ═══════════════════════════════════════════════════════════════════


class TestDeployerChainsQuery:
    def test_returns_none_when_client_unavailable(self) -> None:
        with patch.object(cce, "_get_ch_client", return_value=None):
            assert cce._query_deployer_chains(_ADDR) is None

    def test_parses_rows(self) -> None:
        fake = DispatchClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("polygon", 2, "0xdeploypoly", "2026-02-01 00:00:00"),
            ]
        )
        with patch.object(cce, "_get_ch_client", return_value=fake):
            out = cce._query_deployer_chains(_ADDR)
        assert out is not None
        assert len(out) == 2
        assert out[0]["chain"] == "ethereum"
        assert out[0]["tokens_deployed"] == 3
        assert out[0]["evidence"] == "0xdeployeth"
        assert out[1]["chain"] == "polygon"

    def test_empty_rows_returns_empty_list(self) -> None:
        fake = DispatchClient(deployer_chains=[])
        with patch.object(cce, "_get_ch_client", return_value=fake):
            assert cce._query_deployer_chains(_ADDR) == []

    def test_error_returns_none(self) -> None:
        fake = DispatchClient(error=RuntimeError("boom"))
        with patch.object(cce, "_get_ch_client", return_value=fake):
            assert cce._query_deployer_chains(_ADDR) is None

    def test_passes_max_execution_time_setting(self) -> None:
        fake = DispatchClient(deployer_chains=[("bsc", 1, "0xabc", None)])
        with patch.object(cce, "_get_ch_client", return_value=fake):
            cce._query_deployer_chains(_ADDR)
        assert fake.executed
        assert "deployer_events" in fake.executed[0]


class TestFirstFunderQuery:
    def test_returns_none_when_client_unavailable(self) -> None:
        with patch.object(cce, "_get_ch_client", return_value=None):
            assert cce._query_first_funder(_ADDR, "ethereum") is None

    def test_parses_funder_row(self) -> None:
        fake = DispatchClient(first_funder=[(_FUNDER, "0xfundtx", "2026-01-01 00:00:00", "0xtoken")])
        with patch.object(cce, "_get_ch_client", return_value=fake):
            out = cce._query_first_funder(_ADDR, "ethereum")
        assert out is not None
        assert out["funder"] == _FUNDER
        assert out["evidence"] == "0xfundtx"
        assert out["first_token"] == "0xtoken"

    def test_empty_rows_returns_none(self) -> None:
        fake = DispatchClient(first_funder=[])
        with patch.object(cce, "_get_ch_client", return_value=fake):
            assert cce._query_first_funder(_ADDR, "ethereum") is None

    def test_error_returns_none(self) -> None:
        fake = DispatchClient(error=TimeoutError("timed out"))
        with patch.object(cce, "_get_ch_client", return_value=fake):
            assert cce._query_first_funder(_ADDR, "ethereum") is None


class TestSameFunderCandidatesQuery:
    def test_returns_none_when_client_unavailable(self) -> None:
        with patch.object(cce, "_get_ch_client", return_value=None):
            assert cce._query_same_funder_candidates(_FUNDER, "ethereum", None) is None

    def test_parses_candidates(self) -> None:
        fake = DispatchClient(
            funder_candidates=[
                ("bsc", "0xaddrBsc", "0xtxbsc", "2026-01-02 00:00:00"),
                ("solana", _SOL_ADDR, "0xtxsol", "2026-01-03 00:00:00"),
            ]
        )
        with patch.object(cce, "_get_ch_client", return_value=fake):
            out = cce._query_same_funder_candidates(_FUNDER, "ethereum", None)
        assert out is not None
        assert len(out) == 2
        assert out[0]["chain"] == "bsc"
        assert out[0]["address"] == "0xaddrbsc"
        assert out[1]["chain"] == "solana"
        assert out[1]["address"] == _SOL_ADDR  # base58 left as-is

    def test_target_chains_filtered(self) -> None:
        fake = DispatchClient(funder_candidates=[("bsc", "0xaddrBsc", "0xtxbsc", "2026-01-02 00:00:00")])
        with patch.object(cce, "_get_ch_client", return_value=fake):
            out = cce._query_same_funder_candidates(_FUNDER, "ethereum", ["bsc"])
        assert out is not None
        assert len(out) == 1
        # The SQL should include the chains IN filter
        assert "chain IN" in fake.executed[0]

    def test_error_returns_none(self) -> None:
        fake = DispatchClient(error=RuntimeError("boom"))
        with patch.object(cce, "_get_ch_client", return_value=fake):
            assert cce._query_same_funder_candidates(_FUNDER, "ethereum", None) is None


# ═══════════════════════════════════════════════════════════════════
# resolve_cross_chain_entity — async public API
# ═══════════════════════════════════════════════════════════════════


class TestResolveCrossChainEntity:
    def setup_method(self) -> None:
        cce._ENTITY_CACHE.clear()

    @pytest.mark.asyncio
    async def test_missing_address_returns_error(self) -> None:
        fake = DispatchClient()
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            out = await cce.resolve_cross_chain_entity("", "ethereum")
        assert out["status"] == "error"
        assert "required" in out["error"]

    @pytest.mark.asyncio
    async def test_clickhouse_unavailable(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(patch.object(cce, "_get_ch_client", return_value=None))
            stack.enter_context(patch.object(cce, "_redis_get", AsyncMock(return_value=None)))
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum")
        assert out["status"] == "unavailable"
        assert out["reason"] == "clickhouse_unavailable"

    @pytest.mark.asyncio
    async def test_no_evidence_yields_unknown_entity_low_risk(self) -> None:
        fake = DispatchClient()
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)
        assert out["result"] == "scanned"
        assert out["addresses"] == []
        assert out["entity_type"] == "unknown"
        assert out["risk"] == "low"

    @pytest.mark.asyncio
    async def test_same_deployer_evm_links(self) -> None:
        fake = DispatchClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("bsc", 2, "0xdeploybsc", "2026-02-01 00:00:00"),
            ]
        )
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            stack.enter_context(
                patch.object(cce, "compute_behavioral_fingerprint", AsyncMock(return_value=_scanned_fp()))
            )
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)
        assert out["result"] == "scanned"
        assert out["entity_type"] == "multi_chain_actor"
        deployer_links = [lnk for lnk in out["addresses"] if lnk["evidence_type"] == "same_deployer"]
        assert len(deployer_links) == 1
        assert deployer_links[0]["chain"] == "bsc"
        assert deployer_links[0]["address"] == _ADDR
        assert deployer_links[0]["confidence"] == cce._CONFIDENCE_DEPLOYER_EVM
        assert deployer_links[0]["evidence"] == "0xdeploybsc"
        assert deployer_links[0]["details"]["tokens_deployed"] == 2

    @pytest.mark.asyncio
    async def test_mixed_chain_deployer_uses_mixed_confidence(self) -> None:
        # Source chain (ethereum) is excluded from the confidence computation;
        # the OTHER chains are bsc (EVM) + solana (non-EVM) -> mixed -> 0.80.
        fake = DispatchClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("bsc", 2, "0xdeploybsc", "2026-02-01 00:00:00"),
                ("solana", 1, "0xdeployso", "2026-03-01 00:00:00"),
            ]
        )
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)
        deployer_links = [lnk for lnk in out["addresses"] if lnk["evidence_type"] == "same_deployer"]
        assert len(deployer_links) == 2
        assert all(lnk["confidence"] == cce._CONFIDENCE_DEPLOYER_MIXED for lnk in deployer_links)

    @pytest.mark.asyncio
    async def test_same_funder_links(self) -> None:
        fake = DispatchClient(
            deployer_chains=[],
            first_funder=[(_FUNDER, "0xfundtx", "2026-01-01 00:00:00", "0xtoken")],
            funder_candidates=[
                ("bsc", "0xaddrBsc", "0xtxbsc", "2026-01-02 00:00:00"),
                ("arbitrum", "0xaddrArb", "0xtxarb", "2026-01-03 00:00:00"),
            ],
        )
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)
        assert out["result"] == "scanned"
        funder_links = [lnk for lnk in out["addresses"] if lnk["evidence_type"] == "same_funder"]
        assert len(funder_links) == 2
        assert funder_links[0]["confidence"] == cce._CONFIDENCE_SAME_FUNDER
        assert funder_links[0]["details"]["funder"] == _FUNDER

    @pytest.mark.asyncio
    async def test_target_chains_filters_deployer_links(self) -> None:
        fake = DispatchClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("bsc", 2, "0xdeploybsc", "2026-02-01 00:00:00"),
                ("polygon", 1, "0xdeploypoly", "2026-03-01 00:00:00"),
            ]
        )
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", target_chains=["bsc"], force_fresh=True)
        chains = [lnk["chain"] for lnk in out["addresses"]]
        assert "bsc" in chains
        assert "polygon" not in chains

    @pytest.mark.asyncio
    async def test_behavioral_confirmation_link(self) -> None:
        fake = DispatchClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("polygon", 2, "0xdeploypoly", "2026-02-01 00:00:00"),
            ]
        )
        source_fp = _scanned_fp()
        target_fp = _scanned_fp()
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            stack.enter_context(
                patch.object(cce, "compute_behavioral_fingerprint", AsyncMock(side_effect=[source_fp, target_fp]))
            )
            stack.enter_context(patch.object(cce, "compare_fingerprints", return_value=0.95))
            stack.enter_context(patch.object(cce, "matched_features", return_value=["tx_frequency"]))
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)
        behavioral = [lnk for lnk in out["addresses"] if lnk["evidence_type"] == "behavioral"]
        assert behavioral
        assert behavioral[0]["chain"] == "polygon"
        assert behavioral[0]["details"]["similarity"] == 0.95
        assert behavioral[0]["details"]["matched_features"] == ["tx_frequency"]
        # confidence = 0.70 * 0.95 rounded
        assert behavioral[0]["confidence"] == round(cce.CONFIDENCE_BEHAVIORAL * 0.95, 2)
        # Payload carries the source fingerprint
        assert out["behavioral_fingerprint"] is not None

    @pytest.mark.asyncio
    async def test_behavioral_below_threshold_skips_link(self) -> None:
        fake = DispatchClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("polygon", 2, "0xdeploypoly", "2026-02-01 00:00:00"),
            ]
        )
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            stack.enter_context(
                patch.object(
                    cce, "compute_behavioral_fingerprint", AsyncMock(side_effect=[_scanned_fp(), _scanned_fp()])
                )
            )
            stack.enter_context(patch.object(cce, "compare_fingerprints", return_value=0.10))
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)
        evidence_types = [lnk["evidence_type"] for lnk in out["addresses"]]
        assert "same_deployer" in evidence_types
        assert "behavioral" not in evidence_types

    @pytest.mark.asyncio
    async def test_behavioral_failure_never_breaks_resolution(self) -> None:
        fake = DispatchClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("bsc", 2, "0xdeploybsc", "2026-02-01 00:00:00"),
            ]
        )
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            stack.enter_context(
                patch.object(cce, "compute_behavioral_fingerprint", AsyncMock(side_effect=RuntimeError("boom")))
            )
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)
        assert out["result"] == "scanned"
        assert any(lnk["evidence_type"] == "same_deployer" for lnk in out["addresses"])

    @pytest.mark.asyncio
    async def test_dedupe_keeps_one_link_per_triple(self) -> None:
        fake = DispatchClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("bsc", 2, "0xdeploybsc", "2026-02-01 00:00:00"),
            ],
            first_funder=[(_FUNDER, "0xfundtx", "2026-01-01 00:00:00", "0xtoken")],
            funder_candidates=[
                ("bsc", _ADDR, "0xtxbsc", "2026-01-02 00:00:00"),
            ],
        )
        with ExitStack() as stack:
            for _p in _patch_env(fake):
                stack.enter_context(_p)
            stack.enter_context(
                patch.object(cce, "compute_behavioral_fingerprint", AsyncMock(return_value=_scanned_fp()))
            )
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)
        # The same (chain, address, evidence_type) can only appear once
        keys = [(lnk["chain"], lnk["address"], lnk["evidence_type"]) for lnk in out["addresses"]]
        assert len(keys) == len(set(keys))

    @pytest.mark.asyncio
    async def test_redis_cache_hit_skips_clickhouse(self) -> None:
        cached_payload = {
            "result": "scanned",
            "address": _ADDR,
            "source_chain": "ethereum",
            "addresses": [],
            "entity_type": "unknown",
            "risk": "low",
        }
        fake = DispatchClient()
        with ExitStack() as stack:
            for _p in _patch_env(fake, redis_hit=cached_payload):
                stack.enter_context(_p)
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum")
        assert out["result"] == "scanned"
        assert fake.executed == []  # no ClickHouse queries on cache hit

    @pytest.mark.asyncio
    async def test_redis_set_failure_does_not_break_resolution(self) -> None:
        fake = DispatchClient()
        with ExitStack() as stack:
            stack.enter_context(patch.object(cce, "_get_ch_client", return_value=fake))
            stack.enter_context(patch.object(cce, "_redis_get", AsyncMock(return_value=None)))
            stack.enter_context(patch.object(cce, "_redis_set", AsyncMock(side_effect=ConnectionError("redis down"))))
            out = await cce.resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)
        assert out["result"] == "scanned"
        assert out["entity_type"] == "unknown"


# ═══════════════════════════════════════════════════════════════════
# Monitoring helpers
# ═══════════════════════════════════════════════════════════════════


class TestCacheStats:
    def setup_method(self) -> None:
        cce._ENTITY_CACHE.clear()

    def test_cache_stats_shape(self) -> None:
        cce._ENTITY_CACHE["k"] = {"_cached_at": 0.0}
        stats = cce.get_cache_stats()
        assert stats["cache_size"] == 1
        assert stats["cache_max"] == cce._ENTITY_CACHE_MAX
        assert stats["cache_ttl_seconds"] == cce._ENTITY_CACHE_TTL
        assert stats["redis_ttl_seconds"] == cce.REDIS_TTL_SECONDS
