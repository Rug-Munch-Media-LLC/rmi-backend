"""Unit tests for deployer_fingerprint — C7 deployer behavioral fingerprint.

Covers ``fingerprint_deployer()`` with a fake ClickHouse client across all
four wallet_memory tables:

  - The 5 risk buckets (NEW / REPEAT_CLEAN / REPEAT_RUG / FARM / PROLIFIC)
  - Success-rate calculation (matured tokens only)
  - Funding analysis (CEX detection, fresh wallet)
  - Median token lifespan calculation
  - Redis fast-cache path
  - No-history graceful degradation

All external dependencies (ClickHouse, Redis) are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.deployer_fingerprint import (
    _assign_risk_bucket,
    _compute_risk_score,
    clear_cache,
    fingerprint_deployer,
)

_DEPLOYER = "0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddead"
_FUNDER = "0xfunderfunderfunderfunderfunderfunderfunder"
_NATIVE_TOKEN = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


def _naive_dt(days_ago: float = 0.0) -> datetime:
    """Naive UTC datetime relative to now (matches engine's now_naive)."""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)


class FakeCHClient:
    """Sync ClickHouse stand-in dispatching on fingerprint SQL fragments."""

    def __init__(
        self,
        stats: list[tuple] | None = None,
        tokens: list[tuple] | None = None,
        dex: list[tuple] | None = None,
        funder_native: list[tuple] | None = None,
        funder_fallback: list[tuple] | None = None,
        funder_labels: list[tuple] | None = None,
        funder_tx_count: list[tuple] | None = None,
        same_funder_deployers: list[tuple] | None = None,
        same_funder_overlap: list[tuple] | None = None,
    ) -> None:
        self.stats = stats
        self.tokens = tokens or []
        self.dex = dex or []
        self.funder_native = funder_native
        self.funder_fallback = funder_fallback
        self.funder_labels = funder_labels or []
        self.funder_tx_count = funder_tx_count or [(0,)]
        self.same_funder_deployers = same_funder_deployers or []
        self.same_funder_overlap = same_funder_overlap or [(0,)]
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        # Order matters: check the most specific fragments first.
        if "count() AS total_deployments" in sql:
            return self.stats or []
        if "first_dex_ts" in sql:  # _query_token_dex_stats
            return self.dex
        if "GROUP BY token_address" in sql:  # _query_per_token_deployments
            return self.tokens
        if "lower(token_address) IN" in sql:  # native first-incoming
            return self.funder_native or []
        if "GROUP BY from_address" in sql:  # any first-incoming (fallback)
            return self.funder_fallback or []
        if "wallet_memory.wallet_labels" in sql and "WHERE address = %(addr)s" in sql:
            return self.funder_labels
        if "LIMIT 11" in sql:  # _query_funder_tx_count
            return self.funder_tx_count
        if "SELECT DISTINCT deployer" in sql:  # same-funder deployer list
            return self.same_funder_deployers
        if "count(DISTINCT to_address)" in sql:  # same-funder overlap
            return self.same_funder_overlap
        return []


def _mock_ch(fake: FakeCHClient):
    return patch("app.domains.scanner.engines.deployer_fingerprint._get_ch_client", return_value=fake)


def _no_redis_get():
    return patch("app.domains.scanner.engines.deployer_fingerprint._redis_get", AsyncMock(return_value=None))


def _no_redis_set():
    return patch("app.domains.scanner.engines.deployer_fingerprint._redis_set", AsyncMock(return_value=True))


# ── Fixture builders ───────────────────────────────────────────────────────


def _stats_row(total: int, first_ts: datetime | None, last_ts: datetime | None = None) -> list[tuple]:
    """Deployer stats aggregate row (count, tokens, chains, first_ts, last_ts)."""
    return [(total, total, ["ethereum"], first_ts, last_ts or first_ts)]


def _token_row(addr: str, deployed_ts: datetime, tx: str = "0xdeploy") -> tuple:
    return (addr, "ethereum", deployed_ts, tx)


def _dex_row(addr: str, first_dex_ts: datetime, last_swap_ts: datetime, count: int = 100) -> tuple:
    return (addr, first_dex_ts, last_swap_ts, count)


def _survived_token(addr: str, deployed_days: float) -> tuple:
    """Dex row for a token deployed N days ago that survived well past 7 days."""
    return _dex_row(addr, _naive_dt(deployed_days - 1.0), _naive_dt(deployed_days - 10.0))


class TestFingerprintBuckets:
    """Bucket assignment driven end-to-end through fingerprint_deployer."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_new_bucket_for_recent_first_deployment(self) -> None:
        """First deployment < 7 days ago -> NEW (score 50)."""
        deployed = _naive_dt(days_ago=2)
        fake = FakeCHClient(
            stats=_stats_row(1, deployed),
            tokens=[_token_row("0xaaa1", deployed)],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out["result"] == "scanned"
        assert out["risk_bucket"] == "NEW"
        assert out["risk_score"] == 50
        assert out["total_deployments"] == 1

    @pytest.mark.asyncio
    async def test_repeat_clean_bucket(self) -> None:
        """Multiple mature deployments that all survived 7+ days -> REPEAT_CLEAN."""
        t1 = _naive_dt(days_ago=30)
        t2 = _naive_dt(days_ago=28)
        addr1, addr2 = "0xaaa1", "0xaaa2"
        fake = FakeCHClient(
            stats=_stats_row(2, t2, t1),
            tokens=[_token_row(addr1, t1), _token_row(addr2, t2)],
            dex=[
                _dex_row(addr1, _naive_dt(29), _naive_dt(20)),
                _dex_row(addr2, _naive_dt(27), _naive_dt(18)),
            ],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out["risk_bucket"] == "REPEAT_CLEAN"
        assert out["risk_score"] == 15
        assert out["success_rate"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_repeat_rug_bucket(self) -> None:
        """Any matured deployment without 7-day survival -> REPEAT_RUG."""
        addr1, addr2 = "0xaaa1", "0xaaa2"
        fake = FakeCHClient(
            stats=_stats_row(2, _naive_dt(30)),
            tokens=[_token_row(addr1, _naive_dt(30)), _token_row(addr2, _naive_dt(28))],
            dex=[
                _dex_row(addr1, _naive_dt(29), _naive_dt(20)),  # survived
                _dex_row(addr2, _naive_dt(27), _naive_dt(26)),  # died within 7 days
            ],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out["risk_bucket"] == "REPEAT_RUG"
        assert out["risk_score"] >= 85

    @pytest.mark.asyncio
    async def test_farm_bucket_same_non_cex_funder(self) -> None:
        """>3 deployments funded by the same non-CEX wallet -> FARM."""
        addr_list = [f"0xfarm{i:04x}" for i in range(5)]
        fake = FakeCHClient(
            stats=_stats_row(5, _naive_dt(30), _naive_dt(5)),
            tokens=[_token_row(a, _naive_dt(30 - i)) for i, a in enumerate(addr_list)],
            dex=[_survived_token(a, 30 - i) for i, a in enumerate(addr_list)],
            funder_native=[
                (_FUNDER, _naive_dt(40), "0xfundtx", _NATIVE_TOKEN, "ETH"),
            ],
            funder_labels=[],  # no exchange label -> non-CEX funder
            funder_tx_count=[(4,)],
            same_funder_deployers=[("0xd1",), ("0xd2",)],
            same_funder_overlap=[(3,)],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out["risk_bucket"] == "FARM"
        # FARM score: 80 + 10 (fresh funder) + 5 (same funder multiple) = 95
        assert out["risk_score"] == 95
        assert out["funder_profile"]["funder_address"] == _FUNDER
        assert out["funder_profile"]["funder_is_cex"] is False

    @pytest.mark.asyncio
    async def test_prolific_bucket_high_volume_low_rug_rate(self) -> None:
        """>50 deployments with success_rate >= 0.8 -> PROLIFIC."""
        n = 60
        addr_list = [f"0xprol{i:04x}" for i in range(n)]
        tokens = []
        dex = []
        for i, a in enumerate(addr_list):
            deployed = _naive_dt(120 - i)
            tokens.append(_token_row(a, deployed))
            dex.append(_dex_row(a, deployed + timedelta(days=5), deployed + timedelta(days=10)))
        fake = FakeCHClient(
            stats=_stats_row(n, _naive_dt(120), _naive_dt(1)),
            tokens=tokens,
            dex=dex,
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out["risk_bucket"] == "PROLIFIC"
        assert out["total_deployments"] == n
        assert out["success_rate"] == pytest.approx(1.0)
        assert out["risk_score"] == 15


class TestSuccessRateAndLifespan:
    """Numerical aggregates (success rate, median lifespan)."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_success_rate_counts_only_matured_tokens(self) -> None:
        """Immature tokens are never counted against the success rate."""
        addr1, addr2, addr3, addr4 = "0xaaa1", "0xaaa2", "0xaaa3", "0xaaa4"
        fake = FakeCHClient(
            stats=_stats_row(4, _naive_dt(30)),
            tokens=[
                _token_row(addr1, _naive_dt(30)),
                _token_row(addr2, _naive_dt(30)),
                _token_row(addr3, _naive_dt(30)),
                _token_row(addr4, _naive_dt(2)),  # too young to judge
            ],
            dex=[
                _dex_row(addr1, _naive_dt(29), _naive_dt(20)),  # survived
                _dex_row(addr2, _naive_dt(29), _naive_dt(20)),  # survived
                _dex_row(addr3, _naive_dt(29), _naive_dt(26)),  # failed
                _dex_row(addr4, _naive_dt(1), _naive_dt(1)),
            ],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out["matured_tokens"] == 3  # young token excluded
        assert out["survived_7d"] == 2
        assert out["success_rate"] == pytest.approx(0.667, abs=0.001)

    @pytest.mark.asyncio
    async def test_median_lifespan_calculation(self) -> None:
        """Median of [10h, 20h, 30h] lifespans is 20h."""
        fdex1 = _naive_dt(days_ago=25)
        fdex2 = _naive_dt(days_ago=25)
        fdex3 = _naive_dt(days_ago=25)
        addr1, addr2, addr3 = "0xaaa1", "0xaaa2", "0xaaa3"
        fake = FakeCHClient(
            stats=_stats_row(3, _naive_dt(30)),
            tokens=[
                _token_row(addr1, _naive_dt(30)),
                _token_row(addr2, _naive_dt(30)),
                _token_row(addr3, _naive_dt(30)),
            ],
            dex=[
                _dex_row(addr1, fdex1, fdex1 + timedelta(hours=10)),
                _dex_row(addr2, fdex2, fdex2 + timedelta(hours=20)),
                _dex_row(addr3, fdex3, fdex3 + timedelta(hours=30)),
            ],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out["median_lifespan_hours"] == pytest.approx(20.0)
        # Lifespan evidence recorded per token
        lifespans = sorted(t["lifespan_hours"] for t in out["evidence"] if t["kind"] == "token")
        assert lifespans == [10.0, 20.0, 30.0]


class TestFunderProfile:
    """Funding analysis: CEX detection and fresh-wallet detection."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_funder_cex_detection(self) -> None:
        """A funder labeled as an exchange is flagged funder_is_cex."""
        addr = "0xaaa1"
        fake = FakeCHClient(
            stats=_stats_row(1, _naive_dt(30)),
            tokens=[_token_row(addr, _naive_dt(30))],
            dex=[_survived_token(addr, 30)],
            funder_native=[(_FUNDER, _naive_dt(40), "0xfundtx", _NATIVE_TOKEN, "ETH")],
            funder_labels=[(_FUNDER, 1, "Binance", "exchange", "", "eth_labels")],
            funder_tx_count=[(8,)],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        profile = out["funder_profile"]
        assert profile["funder_address"] == _FUNDER
        assert profile["funder_is_cex"] is True
        assert profile["cex_label"] == "Binance"
        assert profile["funding_native"] is True

    @pytest.mark.asyncio
    async def test_funder_fresh_wallet_detection(self) -> None:
        """A funder with < 10 transactions is flagged funder_is_fresh."""
        addr = "0xaaa1"
        fake = FakeCHClient(
            stats=_stats_row(1, _naive_dt(30)),
            tokens=[_token_row(addr, _naive_dt(30))],
            dex=[_survived_token(addr, 30)],
            funder_native=[(_FUNDER, _naive_dt(40), "0xfundtx", _NATIVE_TOKEN, "ETH")],
            funder_labels=[],  # no exchange label
            funder_tx_count=[(3,)],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        profile = out["funder_profile"]
        assert profile["funder_is_cex"] is False
        assert profile["funder_tx_count"] == 3
        assert profile["funder_is_fresh"] is True

    @pytest.mark.asyncio
    async def test_no_funder_keeps_profile_empty(self) -> None:
        """Without any incoming transfer the funder profile stays unset."""
        addr = "0xaaa1"
        fake = FakeCHClient(
            stats=_stats_row(1, _naive_dt(30)),
            tokens=[_token_row(addr, _naive_dt(30))],
            dex=[_survived_token(addr, 30)],
        )
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out["funder_profile"]["funder_address"] is None


class TestCachingAndDegradation:
    """Redis fast path and graceful degradation."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_redis_cache_hit_skips_computation(self) -> None:
        """A cached fingerprint is returned without running ClickHouse queries."""
        cached = {
            "check": "deployer_fingerprint",
            "result": "scanned",
            "deployer": _DEPLOYER,
            "chain": "ethereum",
            "risk_bucket": "REPEAT_CLEAN",
            "risk_score": 15,
            "total_deployments": 2,
        }
        with (
            patch("app.domains.scanner.engines.deployer_fingerprint._redis_get", AsyncMock(return_value=cached)),
            patch(
                "app.domains.scanner.engines.deployer_fingerprint._compute_fingerprint",
                AsyncMock(),
            ) as compute,
        ):
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out == cached
        compute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_history_returns_unavailable(self) -> None:
        """Deployer with zero deployments degrades gracefully."""
        fake = FakeCHClient(stats=_stats_row(0, None))
        with _mock_ch(fake), _no_redis_get(), _no_redis_set():
            out = await fingerprint_deployer(_DEPLOYER, "ethereum")
        assert out["result"] == "unavailable"
        assert out["risk_bucket"] == "NEW"
        assert out["risk_score"] == 50
        assert out["total_deployments"] == 0


class TestLaunchpadFactoryResolution:
    """Launchpad factories are resolved to the real creator before fingerprinting."""

    _PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    _REAL_CREATOR = "7GVW8rU9NqJhYzVpLf3kQxWmAbCdEfGhIjKlMnOpQrStU"

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_factory_resolves_to_real_creator_before_fingerprint(self) -> None:
        """The fingerprint is computed on the resolved creator, not the factory."""
        fake = FakeCHClient(
            stats=_stats_row(2, _naive_dt(days=30)),
            tokens=[_token_row("0xcccccccccccccccccccccccccccccccccccccccc", _naive_dt(days=20))],
        )
        resolved = {
            "real_deployer": self._REAL_CREATOR,
            "launchpad": "pump.fun",
            "confidence": 0.9,
            "evidence_tx": "0xabc",
        }
        with (
            _mock_ch(fake),
            _no_redis_get(),
            _no_redis_set(),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.resolve_launchpad_deployer",
                AsyncMock(return_value=resolved),
            ),
            patch(
                "app.domains.scanner.engines.launchpad_resolver._query_factory_latest_deploy_tx",
                return_value="0xabc",
            ),
        ):
            out = await fingerprint_deployer(self._PUMP_FUN, "solana")

        assert out["result"] == "scanned"
        assert out["launchpad"] == "pump.fun"
        assert out["real_deployer"] == self._REAL_CREATOR
        assert out["launchpad_resolved"] is True
        assert out["deployer"] == self._REAL_CREATOR  # fingerprint of the real creator
        assert out["risk_bucket"] == "REPEAT_CLEAN"  # from the creator's history

    @pytest.mark.asyncio
    async def test_factory_unresolved_returns_unavailable_not_factory_score(self) -> None:
        """An unresolved factory never gets fingerprinted (aggregate score is misleading)."""
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.resolve_launchpad_deployer",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.domains.scanner.engines.launchpad_resolver._query_factory_latest_deploy_tx",
                return_value=None,
            ),
        ):
            out = await fingerprint_deployer(self._PUMP_FUN, "solana")

        assert out["result"] == "unavailable"
        assert out["launchpad"] == "pump.fun"
        assert out["launchpad_resolved"] is False
        assert "real creator" in (out.get("note") or "")


class TestAssignRiskBucket:
    """Unit tests for the bucket-assignment precedence."""

    def test_all_five_buckets_assigned(self) -> None:
        """Each documented bucket is produced from its defining metrics."""
        now = _naive_dt(days_ago=0)
        cases = [
            ({"total_deployments": 1, "first_seen": now - timedelta(days=1)}, "NEW"),
            ({"total_deployments": 2, "first_seen": now - timedelta(days=30), "any_rug": False}, "REPEAT_CLEAN"),
            ({"total_deployments": 2, "first_seen": now - timedelta(days=30), "any_rug": True}, "REPEAT_RUG"),
            (
                {
                    "total_deployments": 5,
                    "first_seen": now - timedelta(days=30),
                    "funder_profile": {"funder_address": _FUNDER, "funder_is_cex": False},
                },
                "FARM",
            ),
            (
                {"total_deployments": 60, "first_seen": now - timedelta(days=30), "success_rate": 0.9},
                "PROLIFIC",
            ),
        ]
        for metrics, expected in cases:
            assert _assign_risk_bucket(metrics, now) == expected, metrics

    def test_precedence_rules(self) -> None:
        """PROLIFIC beats FARM/REPEAT_RUG; FARM beats REPEAT_RUG; NEW is first."""
        now = _naive_dt(days_ago=0)
        # PROLIFIC wins over FARM
        assert (
            _assign_risk_bucket(
                {
                    "total_deployments": 60,
                    "first_seen": now - timedelta(days=30),
                    "success_rate": 0.9,
                    "funder_profile": {"funder_address": _FUNDER, "funder_is_cex": False},
                },
                now,
            )
            == "PROLIFIC"
        )
        # FARM wins over REPEAT_RUG
        assert (
            _assign_risk_bucket(
                {
                    "total_deployments": 5,
                    "first_seen": now - timedelta(days=30),
                    "any_rug": True,
                    "funder_profile": {"funder_address": _FUNDER, "funder_is_cex": False},
                },
                now,
            )
            == "FARM"
        )
        # CEX funder never triggers FARM
        assert (
            _assign_risk_bucket(
                {
                    "total_deployments": 5,
                    "first_seen": now - timedelta(days=30),
                    "any_rug": True,
                    "funder_profile": {"funder_address": _FUNDER, "funder_is_cex": True},
                },
                now,
            )
            == "REPEAT_RUG"
        )
        # Recent first deployment beats everything
        assert (
            _assign_risk_bucket(
                {
                    "total_deployments": 60,
                    "first_seen": now - timedelta(days=1),
                    "success_rate": 0.9,
                },
                now,
            )
            == "NEW"
        )


class TestComputeRiskScore:
    """Unit tests for the bucket -> 0-100 score mapping."""

    def test_risk_score_mapping(self) -> None:
        """Each bucket maps to its documented score."""
        assert _compute_risk_score("NEW", {"total_deployments": 1}) == 50
        assert _compute_risk_score("REPEAT_CLEAN", {"total_deployments": 2}) == 15
        # 1 rug of 2 matured -> 85 + 2 = 87
        assert (
            _compute_risk_score(
                "REPEAT_RUG",
                {"total_deployments": 2, "survived_count": 1, "matured_count": 2},
            )
            == 87
        )
        # Fresh funder (+10) and same-funder-multiple (+5) -> 95
        assert (
            _compute_risk_score(
                "FARM",
                {"funder_profile": {"funder_is_fresh": True, "same_funder_multiple": True}},
            )
            == 95
        )
        # Very high success rate shaves PROLIFIC score down to floor
        assert (
            _compute_risk_score(
                "PROLIFIC",
                {"total_deployments": 60, "survived_count": 58, "matured_count": 60, "success_rate": 0.967},
            )
            == 15
        )


if __name__ == "__main__":
    pytest.main([__file__])
