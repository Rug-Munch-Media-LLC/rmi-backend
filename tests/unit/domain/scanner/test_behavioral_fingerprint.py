"""Unit tests for behavioral_fingerprint — C4 v2 cross-chain entity matching.

Covers ``compute_behavioral_fingerprint()`` and ``compare_fingerprints()`` with
a fake ClickHouse client across the wallet_memory tables:

  - Feature extraction (frequency, value, DEX, temporal histograms)
  - Cosine similarity between fingerprints (identical vs dissimilar)
  - Matched-feature explainability
  - No-history graceful degradation
  - C4 integration: behavioral confirmation links in cross_chain_entity

All external dependencies (ClickHouse, Redis) are mocked.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.behavioral_fingerprint import (
    BEHAVIORAL_MIN_SIMILARITY,
    clear_cache,
    compare_fingerprints,
    compute_behavioral_fingerprint,
    matched_features,
)

_ADDR = "0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddead"


class FakeCHClient:
    """Sync ClickHouse stand-in dispatching on behavioral SQL fragments."""

    def __init__(
        self,
        frequency: list[tuple] | None = None,
        burst: list[tuple] | None = None,
        value: list[tuple] | None = None,
        dex: list[tuple] | None = None,
        protocols: list[tuple] | None = None,
        hours: list[tuple] | None = None,
        dow: list[tuple] | None = None,
        deployer_chains: list[tuple] | None = None,
        first_funder: list[tuple] | None = None,
        funder_candidates: list[tuple] | None = None,
    ) -> None:
        self.frequency = frequency or []
        self.burst = burst or []
        self.value = value or []
        self.dex = dex or []
        self.protocols = protocols or []
        self.hours = hours or []
        self.dow = dow or []
        self.deployer_chains = deployer_chains or []
        self.first_funder = first_funder or []
        self.funder_candidates = funder_candidates or []
        # Optional per-chain behavioral rows for cross-chain tests:
        #   {"ethereum": {"frequency": [...], "burst": [...], ...}}
        self.behavioral_by_chain = {}
        self.executed: list[str] = []

    def set_behavioral_by_chain(self, data: dict) -> None:
        self.behavioral_by_chain = data

    def _behavioral_attr(self, sql: str, params: dict | None, attr: str):
        chain = (params or {}).get("chain", "")
        if chain in self.behavioral_by_chain and attr in self.behavioral_by_chain[chain]:
            return self.behavioral_by_chain[chain][attr]
        return getattr(self, attr)

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        # Behavioral fingerprint queries (specific fragments first)
        if "max(c) AS max_day" in sql:  # _query_burst
            return self._behavioral_attr(sql, params, "burst")
        if "quantile(0.5)(toFloat64OrZero(value)) AS median_value" in sql:  # _query_value_profile
            return self._behavioral_attr(sql, params, "value")
        if "uniqExact(contract_address) AS dex_diversity" in sql:  # _query_dex_profile
            return self._behavioral_attr(sql, params, "dex")
        if "GROUP BY dex" in sql:  # _query_top_protocols
            return self._behavioral_attr(sql, params, "protocols")
        if "toHour(block_timestamp) AS h" in sql:  # _query_active_hours
            return self._behavioral_attr(sql, params, "hours")
        if "toDayOfWeek(block_timestamp) AS d" in sql:  # _query_active_dow
            return self._behavioral_attr(sql, params, "dow")
        if "uniqExact(toDate(block_timestamp)) AS active_days" in sql:  # _query_frequency
            return self._behavioral_attr(sql, params, "frequency")
        # Cross-chain entity queries
        if "uniqExact(token_address) AS tokens_deployed" in sql:  # _query_deployer_chains
            return self.deployer_chains
        if "ORDER BY first_ts ASC" in sql and "LIMIT 1" in sql:  # _query_first_funder
            return self.first_funder
        if "GROUP BY chain, to_address" in sql:  # _query_same_funder_candidates
            return self.funder_candidates
        return []


def _mock_ch(fake: FakeCHClient):
    return patch("app.domains.scanner.engines.behavioral_fingerprint._get_ch_client", return_value=fake)


def _no_redis_get():
    return patch("app.domains.scanner.engines.behavioral_fingerprint._redis_get", AsyncMock(return_value=None))


def _no_redis_set():
    return patch("app.domains.scanner.engines.behavioral_fingerprint._redis_set", AsyncMock(return_value=True))


# ── Fixture builders ───────────────────────────────────────────────────────

# A busy daily trader: high tx count, active across many days, median ~1 ETH,
# swaps on 2 DEXes, active around 12:00-14:00 UTC, Mon-Fri.
_BUSY_FREQUENCY = [(1200, 5, 1, 90)]  # tx_count, min_ts, max_ts, active_days
_BUSY_BURST = [(60,)]  # busiest single day
_BUSY_VALUE = [(1e18, 5e18)]  # median, p95 (wei)
_BUSY_DEX = [(800, 3, 25)]  # swap_count, dex_diversity, token_diversity
_BUSY_PROTOCOLS = [("uniswap", 500), ("curve", 300)]
_BUSY_HOURS = [(12, 600), (13, 600)]
_BUSY_DOW = [(1, 240), (2, 240), (3, 240), (4, 240), (5, 240)]

# A night-owl weekend sniper: lower frequency, same DEX preference but one
# protocol, median ~0.01 ETH, active 02:00-03:00 UTC, Sat-Sun.
_SNIPER_FREQUENCY = [(60, 5, 1, 5)]
_SNIPER_BURST = [(60,)]  # all activity on one day -> high burst
_SNIPER_VALUE = [(1e16, 1e17)]
_SNIPER_DEX = [(55, 1, 10)]
_SNIPER_PROTOCOLS = [("uniswap", 55)]
_SNIPER_HOURS = [(2, 30), (3, 30)]
_SNIPER_DOW = [(6, 30), (7, 30)]


def _busy_fp_dict() -> dict:
    return {
        "result": "scanned",
        "address": _ADDR,
        "chain": "ethereum",
        "fingerprint": {
            "tx_count": 1200,
            "active_days": 90,
            "tx_per_day": 13.3333,
            "burst_score": 0.05,
            "swap_count": 800,
            "swap_per_day": 8.8889,
            "dex_diversity": 3,
            "token_diversity": 25,
            "top_protocols": ["uniswap", "curve"],
            "median_value": 1e18,
            "p95_value": 5e18,
        },
        "confidence": 1.0,
    }


class TestComputeFingerprint:
    """Feature extraction from ClickHouse rows."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_scanned_fingerprint_extracts_features(self) -> None:
        """All feature groups should be populated from the fake rows."""
        fake = FakeCHClient(
            frequency=_BUSY_FREQUENCY,
            burst=_BUSY_BURST,
            value=_BUSY_VALUE,
            dex=_BUSY_DEX,
            protocols=_BUSY_PROTOCOLS,
            hours=_BUSY_HOURS,
            dow=_BUSY_DOW,
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await compute_behavioral_fingerprint(_ADDR, "ethereum")

        assert out["result"] == "scanned"
        assert out["chain"] == "ethereum"
        fp = out["fingerprint"]
        # Frequency
        assert fp["tx_count"] == 1200
        assert fp["active_days"] == 90
        assert fp["tx_per_day"] == pytest.approx(round(1200 / 90, 4))
        assert fp["burst_score"] == pytest.approx(round(60 / 1200, 4))
        # Value
        assert fp["median_value"] == pytest.approx(1e18)
        assert fp["p95_value"] == pytest.approx(5e18)
        assert fp["outlier_threshold"] == pytest.approx(5e18 * 1.5)
        # DEX
        assert fp["swap_count"] == 800
        assert fp["dex_diversity"] == 3
        assert fp["token_diversity"] == 25
        assert fp["top_protocols"] == ["uniswap", "curve"]
        # Temporal histograms are normalized to 0-1 and fixed-length
        assert len(fp["active_hours"]) == 24
        assert sum(fp["active_hours"]) == pytest.approx(1.0)
        assert len(fp["active_dow"]) == 7
        assert sum(fp["active_dow"]) == pytest.approx(1.0)
        # Gas is [REVIEW] not available from evm_transfers
        assert fp["median_gas"] is None
        assert fp["gas_price_sensitivity"] is None
        assert out["confidence"] > 0.5

    @pytest.mark.asyncio
    async def test_no_history_returns_unavailable(self) -> None:
        """Zero transfers and zero swaps -> unavailable, not a false match."""
        fake = FakeCHClient(
            frequency=[],
            burst=[],
            value=[],
            dex=[],
            protocols=[],
            hours=[],
            dow=[],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await compute_behavioral_fingerprint(_ADDR, "ethereum")
        assert out["result"] == "unavailable"
        assert out["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_empty_chain_returns_error(self) -> None:
        """Missing required args -> error result, never raises."""
        out = await compute_behavioral_fingerprint("", "ethereum")
        assert out["result"] == "error"
        out2 = await compute_behavioral_fingerprint(_ADDR, "")
        assert out2["result"] == "error"

    @pytest.mark.asyncio
    async def test_clickhouse_down_returns_unavailable(self) -> None:
        """No CH client -> graceful unavailable degradation."""
        with (
            patch("app.domains.scanner.engines.behavioral_fingerprint._get_ch_client", return_value=None),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await compute_behavioral_fingerprint(_ADDR, "ethereum")
        assert out["result"] == "unavailable"


class TestCompareFingerprints:
    """Cosine similarity between behavioral fingerprints."""

    def test_identical_fingerprints_high_similarity(self) -> None:
        """Same wallet on same chain -> similarity near 1.0."""
        fp_a = _busy_fp_dict()
        fp_b = dict(_busy_fp_dict())
        sim = compare_fingerprints(fp_a, fp_b)
        assert sim >= 0.99

    def test_dissimilar_fingerprints_low_similarity(self) -> None:
        """Busy day trader vs night-owl sniper -> clearly separated."""
        fp_busy = _busy_fp_dict()
        fp_sniper = {
            "result": "scanned",
            "address": _ADDR,
            "chain": "bsc",
            "fingerprint": {
                "tx_count": 60,
                "active_days": 5,
                "tx_per_day": 12.0,
                "burst_score": 1.0,
                "swap_count": 55,
                "swap_per_day": 11.0,
                "dex_diversity": 1,
                "token_diversity": 10,
                "top_protocols": ["uniswap"],
                "median_value": 1e16,
                "p95_value": 1e17,
                "active_hours": [0.5 if h in (2, 3) else 0.0 for h in range(24)],
                "active_dow": [0.5 if d in (6, 7) else 0.0 for d in range(1, 8)],
            },
            "confidence": 0.9,
        }
        sim = compare_fingerprints(fp_busy, fp_sniper)
        assert sim < 0.7
        assert sim >= 0.0

    def test_similar_trading_style_high_similarity(self) -> None:
        """Two wallets with the same profile (sniper/sniper) match strongly."""
        sniper_a = {
            "result": "scanned",
            "fingerprint": {
                "tx_count": 60,
                "active_days": 5,
                "tx_per_day": 12.0,
                "burst_score": 1.0,
                "swap_count": 55,
                "swap_per_day": 11.0,
                "dex_diversity": 1,
                "token_diversity": 10,
                "top_protocols": ["uniswap"],
                "median_value": 1e16,
                "p95_value": 1e17,
                "active_hours": [0.5 if h in (2, 3) else 0.0 for h in range(24)],
                "active_dow": [0.5 if d in (6, 7) else 0.0 for d in range(1, 8)],
            },
            "confidence": 0.9,
        }
        sniper_b = {
            "result": "scanned",
            "fingerprint": {
                "tx_count": 55,
                "active_days": 5,
                "tx_per_day": 11.0,
                "burst_score": 1.0,
                "swap_count": 50,
                "swap_per_day": 10.0,
                "dex_diversity": 1,
                "token_diversity": 9,
                "top_protocols": ["uniswap"],
                "median_value": 1.2e16,
                "p95_value": 1.4e17,
                "active_hours": [0.5 if h in (2, 3) else 0.0 for h in range(24)],
                "active_dow": [0.5 if d in (6, 7) else 0.0 for d in range(1, 8)],
            },
            "confidence": 0.9,
        }
        sim = compare_fingerprints(sniper_a, sniper_b)
        assert sim > 0.85

    def test_missing_fingerprint_returns_zero(self) -> None:
        """Unavailable fingerprints must not produce a false positive."""
        unavailable = {"result": "unavailable", "fingerprint": None, "confidence": 0.0}
        sim = compare_fingerprints(unavailable, _busy_fp_dict())
        assert sim == 0.0

    def test_threshold_used_by_c4(self) -> None:
        """The C4 integration gate must be a sane 0-1 threshold."""
        assert 0.0 < BEHAVIORAL_MIN_SIMILARITY < 1.0


class TestMatchedFeatures:
    """Explainable feature matching between two fingerprints."""

    def test_identical_wallets_match_core_features(self) -> None:
        """Same wallet matches frequency, DEX diversity, and temporal pattern."""
        fp_a = _busy_fp_dict()
        fp_b = dict(_busy_fp_dict())
        fp_a["fingerprint"]["active_hours"] = [0.5 if h == 12 else 0.0 for h in range(24)]
        fp_a["fingerprint"]["active_dow"] = [0.2 for _ in range(7)]
        fp_b["fingerprint"]["active_hours"] = [0.5 if h == 12 else 0.0 for h in range(24)]
        fp_b["fingerprint"]["active_dow"] = [0.2 for _ in range(7)]
        fp_b["fingerprint"]["top_protocols"] = ["uniswap", "curve", "pancakeswap"]
        matches = matched_features(fp_a, fp_b)
        assert "tx_frequency" in matches
        assert "dex_diversity" in matches
        assert "temporal_pattern" in matches
        assert "protocol_overlap" in matches

    def test_no_history_yields_no_matches(self) -> None:
        """Unavailable fingerprints produce an empty match list."""
        unavailable = {"result": "unavailable", "fingerprint": None, "confidence": 0.0}
        assert matched_features(unavailable, _busy_fp_dict()) == []


class TestCrossChainIntegration:
    """C4 v2: behavioral confirmation links from resolve_cross_chain_entity."""

    def setup_method(self) -> None:
        """Clear in-process caches so fingerprints don't leak across tests."""
        from app.domains.scanner.engines import behavioral_fingerprint as bfp, cross_chain_entity as cce

        bfp.clear_cache()
        cce._ENTITY_CACHE.clear()  # clear_cache() is async; dict clear avoids a dangling coroutine

    def _patch_cross_chain(self, fake: FakeCHClient):
        from app.domains.scanner.engines import behavioral_fingerprint as bfp, cross_chain_entity as cce

        return [
            patch.object(cce, "_get_ch_client", return_value=fake),
            patch.object(cce, "_redis_get", AsyncMock(return_value=None)),
            patch.object(cce, "_redis_set", AsyncMock(return_value=True)),
            patch.object(bfp, "_get_ch_client", return_value=fake),
            patch.object(bfp, "_redis_get", AsyncMock(return_value=None)),
            patch.object(bfp, "_redis_set", AsyncMock(return_value=True)),
        ]

    @pytest.mark.asyncio
    async def test_deployer_candidate_with_behavioral_confirmation(self) -> None:
        """A same-deployer candidate with a matching fingerprint gets a
        ``behavioral`` evidence link with a confidence score."""
        from app.domains.scanner.engines.cross_chain_entity import resolve_cross_chain_entity

        fake = FakeCHClient(
            # Deployer on ethereum + polygon
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("polygon", 2, "0xdeploypoly", "2026-02-01 00:00:00"),
            ],
            first_funder=[],
            funder_candidates=[],
            # Identical behavioral profile on both chains
            frequency=_BUSY_FREQUENCY,
            burst=_BUSY_BURST,
            value=_BUSY_VALUE,
            dex=_BUSY_DEX,
            protocols=_BUSY_PROTOCOLS,
            hours=_BUSY_HOURS,
            dow=_BUSY_DOW,
        )
        with ExitStack() as stack:
            for _p in self._patch_cross_chain(fake):
                stack.enter_context(_p)
            out = await resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)

        assert out["result"] == "scanned"
        evidence_types = [lnk["evidence_type"] for lnk in out["addresses"]]
        assert "same_deployer" in evidence_types
        assert "behavioral" in evidence_types

        behavioral = [lnk for lnk in out["addresses"] if lnk["evidence_type"] == "behavioral"]
        assert behavioral
        link = behavioral[0]
        assert link["chain"] == "polygon"
        assert link["address"] == _ADDR
        # Confidence = behavioral baseline (0.70) x similarity, capped 0-1
        assert 0.0 <= link["confidence"] <= 1.0
        assert "similarity" in link["details"]
        assert link["details"]["similarity"] >= BEHAVIORAL_MIN_SIMILARITY
        assert "matched_features" in link["details"]
        # Payload now carries the real fingerprint, not "coming in v2"
        assert out["behavioral_fingerprint"] is not None
        assert out["behavioral_fingerprint"]["tx_count"] == 1200

    @pytest.mark.asyncio
    async def test_dissimilar_candidate_skips_behavioral_link(self) -> None:
        """Deployer candidate whose behavior differs enough gets no behavioral
        link — the deployer evidence still stands on its own."""
        from app.domains.scanner.engines.cross_chain_entity import resolve_cross_chain_entity

        fake = FakeCHClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("polygon", 2, "0xdeploypoly", "2026-02-01 00:00:00"),
            ],
            first_funder=[],
            funder_candidates=[],
            # Default rows (used for ethereum source chain)
            frequency=_BUSY_FREQUENCY,
            burst=_BUSY_BURST,
            value=_BUSY_VALUE,
            dex=_BUSY_DEX,
            protocols=_BUSY_PROTOCOLS,
            hours=_BUSY_HOURS,
            dow=_BUSY_DOW,
        )
        # Polygon (target) = night-owl sniper profile -> dissimilar
        fake.set_behavioral_by_chain(
            {
                "polygon": {
                    "frequency": _SNIPER_FREQUENCY,
                    "burst": _SNIPER_BURST,
                    "value": _SNIPER_VALUE,
                    "dex": _SNIPER_DEX,
                    "protocols": _SNIPER_PROTOCOLS,
                    "hours": _SNIPER_HOURS,
                    "dow": _SNIPER_DOW,
                }
            }
        )
        with ExitStack() as stack:
            for _p in self._patch_cross_chain(fake):
                stack.enter_context(_p)
            out = await resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)

        assert out["result"] == "scanned"
        evidence_types = [lnk["evidence_type"] for lnk in out["addresses"]]
        assert "same_deployer" in evidence_types
        assert "behavioral" not in evidence_types

    @pytest.mark.asyncio
    async def test_behavioral_failure_never_breaks_resolution(self) -> None:
        """If the fingerprint engine throws, deployer/funder links still resolve."""
        from app.domains.scanner.engines.cross_chain_entity import resolve_cross_chain_entity

        fake = FakeCHClient(
            deployer_chains=[
                ("ethereum", 3, "0xdeployeth", "2026-01-01 00:00:00"),
                ("polygon", 2, "0xdeploypoly", "2026-02-01 00:00:00"),
            ],
            first_funder=[],
            funder_candidates=[],
            frequency=_BUSY_FREQUENCY,
            burst=_BUSY_BURST,
            value=_BUSY_VALUE,
            dex=_BUSY_DEX,
            protocols=_BUSY_PROTOCOLS,
            hours=_BUSY_HOURS,
            dow=_BUSY_DOW,
        )
        with ExitStack() as stack:
            for _p in self._patch_cross_chain(fake):
                stack.enter_context(_p)
            stack.enter_context(
                patch(
                    "app.domains.scanner.engines.cross_chain_entity.compute_behavioral_fingerprint",
                    AsyncMock(side_effect=RuntimeError("boom")),
                )
            )
            out = await resolve_cross_chain_entity(_ADDR, "ethereum", force_fresh=True)

        assert out["result"] == "scanned"
        assert any(lnk["evidence_type"] == "same_deployer" for lnk in out["addresses"])
