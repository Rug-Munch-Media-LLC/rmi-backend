"""Unit tests for repeat deployer detection — deployer_profiles engine.

Covers ``app.domains.scanner.engines.deployer_profiles`` with a fake
ClickHouse client:

  - Repeat-deployer detection (GROUP BY deployer HAVING count > 1)
  - Track-record computation (tokens launched, rugged %, active %, honeypot %,
    avg lifespan, avg liquidity at 24h)
  - The human-readable label format
  - The fast lookup path (Redis → ClickHouse row → on-demand compute)
  - Batch refresh persistence
  - Graceful degradation when ClickHouse is unavailable

All external dependencies (ClickHouse, Redis) are mocked.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.deployer_profiles import (
    MIN_DEPLOYMENTS,
    build_deployer_label,
    clear_cache,
    compute_deployer_profile,
    detect_repeat_deployers,
    get_deployer_profile,
    refresh_deployer_profiles,
)

_DEPLOYER = "0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddead"
_TOKEN_A = "0xaaaa000000000000000000000000000000000001"
_TOKEN_B = "0xbbbb000000000000000000000000000000000002"
_TOKEN_C = "0xcccc000000000000000000000000000000000003"


def _naive_dt(days_ago: float = 0.0) -> datetime:
    """Naive UTC datetime relative to now (matches engine's now_naive)."""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)


class FakeCHClient:
    """Sync ClickHouse stand-in dispatching on deployer_profiles SQL fragments."""

    def __init__(
        self,
        repeat_rows: list[tuple] | None = None,
        stats_rows: list[tuple] | None = None,
        token_rows: list[tuple] | None = None,
        lifecycle_rows: list[tuple] | dict[str, list[tuple]] | None = None,
        profile_rows: list[tuple] | None = None,
        insert_result: object = 1,
    ) -> None:
        self.repeat_rows = repeat_rows or []
        self.stats_rows = stats_rows
        self.token_rows = token_rows or []
        self.lifecycle_rows = lifecycle_rows or []
        self.profile_rows = profile_rows or []
        self.insert_result = insert_result
        self.executed: list[str] = []
        self.inserted_rows: list[list[tuple]] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        # Order matters: check the most specific fragments first.
        if sql.lstrip().startswith("INSERT INTO"):
            # Capture rows for assertions, then report success.
            rows = params or []
            if rows and isinstance(rows, list) and isinstance(rows[0], (list, tuple)):
                self.inserted_rows.append([tuple(r) for r in rows])
            return self.insert_result
        if "CREATE TABLE IF NOT EXISTS" in sql:
            return []
        if "FROM wallet_memory.deployer_profiles" in sql:
            return self.profile_rows
        if "AS volume_24h" in sql:  # _query_batch_token_lifecycle
            return self.lifecycle_rows
        if "AS deployed_ts" in sql:  # _query_deployer_tokens
            return self.token_rows
        if "AS first_ts" in sql:  # _query_deployer_stats
            return self.stats_rows or []
        if "AS tokens_launched" in sql:  # detect_repeat_deployers
            return self.repeat_rows
        return []


def _mock_ch(fake: FakeCHClient):
    return patch("app.domains.scanner.engines.deployer_profiles._get_ch_client", return_value=fake)


def _no_redis() -> ExitStack:
    """Return an ExitStack that disables Redis (get→None, set→True)."""
    stack = ExitStack()
    stack.enter_context(patch("app.domains.scanner.engines.deployer_profiles.redis_get", AsyncMock(return_value=None)))
    stack.enter_context(patch("app.domains.scanner.engines.deployer_profiles.redis_set", AsyncMock(return_value=True)))
    return stack


# ── Fixture builders ───────────────────────────────────────────────────────


def _repeat_row(deployer: str, deployments: int = 5, tokens: int = 4) -> tuple:
    return (
        deployer,
        deployments,
        tokens,
        _naive_dt(days_ago=40),
        _naive_dt(days_ago=2),
        ["ethereum", "base"],
    )


def _stats_row(deployments: int = 5, tokens: int = 4) -> list[tuple]:
    return [
        (
            deployments,
            tokens,
            ["ethereum", "base"],
            _naive_dt(days_ago=40),
            _naive_dt(days_ago=2),
        )
    ]


def _token_row(addr: str, deployed_days: float) -> tuple:
    return (addr, "base", _naive_dt(days_ago=deployed_days))


def _lifecycle_row(
    addr: str = "",
    *,
    swaps: int = 100,
    swaps_48h: int = 5,
    last_swap_days: float = 1.0,
    vol_24h: float = 50_000.0,
    vol_after_72h: float = 5_000.0,
    swaps_after_72h: int = 40,
    buys: int = 40,
    sells: int = 30,
) -> tuple:
    return (
        addr,
        swaps,
        swaps_48h,
        _naive_dt(days_ago=last_swap_days),
        vol_24h,
        vol_after_72h,
        swaps_after_72h,
        buys,
        sells,
    )


def _profile_row(deployer: str = _DEPLOYER) -> tuple:
    return (
        deployer,
        "base",
        4,  # total_tokens_launched
        5,  # total_deployments
        50_000.0,  # avg_liquidity_24h
        0.25,  # rugged_pct
        0.5,  # active_pct
        0.0,  # honeypot_pct
        5.0,  # avg_lifespan_days
        ["base", "ethereum"],
        _naive_dt(days_ago=40),
        _naive_dt(days_ago=2),
    )


# ═══════════════════════════════════════════════════════════════════════════
# LABEL FORMAT
# ═══════════════════════════════════════════════════════════════════════════


class TestDeployerLabel:
    def test_exact_label_format(self) -> None:
        profile = {
            "total_tokens_launched": 15,
            "rugged_pct": 0.40,
            "avg_lifespan_days": 3.2,
        }
        assert build_deployer_label(profile) == "Repeat deployer: 15 tokens, 40% rugged, avg life 3 days"

    def test_label_unknown_lifespan(self) -> None:
        profile = {"total_tokens_launched": 2, "rugged_pct": 0.0, "avg_lifespan_days": None}
        assert "avg life unknown" in build_deployer_label(profile)

    def test_label_hours_for_short_lifespan(self) -> None:
        profile = {"total_tokens_launched": 3, "rugged_pct": 1.0, "avg_lifespan_days": 0.4}
        label = build_deployer_label(profile)
        assert "100% rugged" in label
        assert "10 hours" in label


# ═══════════════════════════════════════════════════════════════════════════
# DETECTION
# ═══════════════════════════════════════════════════════════════════════════


class TestRepeatDeployerDetection:
    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_detect_returns_repeat_deployers(self) -> None:
        fake = FakeCHClient(repeat_rows=[_repeat_row(_DEPLOYER)])
        with _mock_ch(fake):
            out = await detect_repeat_deployers(chain="base")
        assert len(out) == 1
        assert out[0]["deployer"] == _DEPLOYER
        assert out[0]["total_deployments"] == 5
        assert out[0]["tokens_launched"] == 4
        assert "base" in out[0]["chains_used"]

    @pytest.mark.asyncio
    async def test_detect_empty_when_no_ch(self) -> None:
        with patch("app.domains.scanner.engines.deployer_profiles._get_ch_client", return_value=None):
            out = await detect_repeat_deployers()
        assert out == []


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeProfile:
    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_compute_full_track_record(self) -> None:
        """Mixed token outcomes produce correct rugged/active/honeypot rates."""
        # Token A: mature, active, not rugged (vol after 72h > $1K), not honeypot
        # Token B: mature, rugged (vol after 72h < $1K), no recent activity
        # Token C: too young to judge (24h old), active, honeypot (buys no sells)
        fake = FakeCHClient(
            stats_rows=_stats_row(deployments=3, tokens=3),
            token_rows=[
                _token_row(_TOKEN_A, deployed_days=30),
                _token_row(_TOKEN_B, deployed_days=30),
                _token_row(_TOKEN_C, deployed_days=1),
            ],
            lifecycle_rows=[
                # Token A: mature, active, not rugged (swaps after 72h), not honeypot
                _lifecycle_row(
                    _TOKEN_A,
                    swaps=200,
                    swaps_48h=10,
                    vol_24h=100_000.0,
                    vol_after_72h=50_000.0,
                    swaps_after_72h=120,
                    buys=90,
                    sells=80,
                ),
                # Token B: mature, rugged (zero swaps after 72h), no recent activity
                _lifecycle_row(
                    _TOKEN_B,
                    swaps=10,
                    swaps_48h=0,
                    vol_24h=500.0,
                    vol_after_72h=0.0,
                    swaps_after_72h=0,
                    buys=8,
                    sells=2,
                ),
                # Token C: too young to judge (24h old), active, honeypot (buys no sells)
                _lifecycle_row(
                    _TOKEN_C,
                    swaps=60,
                    swaps_48h=20,
                    vol_24h=20_000.0,
                    vol_after_72h=0.0,
                    swaps_after_72h=0,
                    buys=60,
                    sells=0,
                ),
            ],
        )
        with _mock_ch(fake), _no_redis():
            out = await compute_deployer_profile(_DEPLOYER, "base")

        assert out is not None
        assert out["is_repeat_deployer"] is True
        assert out["total_tokens_launched"] == 3
        assert out["total_deployments"] == 3
        # Mature tokens: A (not rugged), B (rugged) => 1/2 = 0.5
        assert out["rugged_pct"] == 0.5
        # Active: A + C => 2/3
        assert out["active_pct"] == round(2 / 3, 3)
        # Honeypot: C only => 1/3
        assert out["honeypot_pct"] == round(1 / 3, 3)
        assert out["avg_lifespan_days"] is not None
        assert out["avg_lifespan_days"] > 0
        assert out["avg_liquidity_24h"] > 0
        assert "Repeat deployer:" in out["label"]

    @pytest.mark.asyncio
    async def test_compute_single_deployment_returns_none(self) -> None:
        fake = FakeCHClient(stats_rows=_stats_row(deployments=1, tokens=1))
        with _mock_ch(fake), _no_redis():
            out = await compute_deployer_profile(_DEPLOYER, "base")
        assert out is None

    @pytest.mark.asyncio
    async def test_compute_no_history_returns_none(self) -> None:
        fake = FakeCHClient(stats_rows=[])
        with _mock_ch(fake), _no_redis():
            out = await compute_deployer_profile(_DEPLOYER, "base")
        assert out is None

    @pytest.mark.asyncio
    async def test_compute_handles_no_dex_data(self) -> None:
        """Tokens with zero DEX activity are still profiled (all rugged if mature)."""
        fake = FakeCHClient(
            stats_rows=_stats_row(deployments=2, tokens=2),
            token_rows=[_token_row(_TOKEN_A, deployed_days=30), _token_row(_TOKEN_B, deployed_days=30)],
            lifecycle_rows=[],  # no dex_events rows
        )
        with _mock_ch(fake), _no_redis():
            out = await compute_deployer_profile(_DEPLOYER, "base")
        assert out is not None
        # Both mature tokens had no post-72h volume → both rugged
        assert out["rugged_pct"] == 1.0
        assert out["active_pct"] == 0.0
        assert out["avg_lifespan_days"] is None


# ═══════════════════════════════════════════════════════════════════════════
# FAST LOOKUP PATH
# ═══════════════════════════════════════════════════════════════════════════


class TestGetProfile:
    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_redis_hit_skips_ch(self) -> None:
        cached = {
            "check": "deployer_profiles",
            "result": "scanned",
            "deployer": _DEPLOYER,
            "chain": "any",
            "is_repeat_deployer": True,
            "total_tokens_launched": 4,
            "total_deployments": 5,
            "rugged_pct": 0.25,
            "active_pct": 0.5,
            "honeypot_pct": 0.0,
            "avg_lifespan_days": 5.0,
            "avg_liquidity_24h": 50_000.0,
            "chains_used": ["base"],
            "label": "Repeat deployer: 4 tokens, 25% rugged, avg life 5 days",
        }
        fake = FakeCHClient(profile_rows=[])  # should never be hit
        with (
            _mock_ch(fake),
            patch("app.domains.scanner.engines.deployer_profiles.redis_get", AsyncMock(return_value=cached)),
            patch("app.domains.scanner.engines.deployer_profiles.redis_set", AsyncMock(return_value=True)),
        ):
            out = await get_deployer_profile(_DEPLOYER, "base")

        assert out is not None
        assert out["is_repeat_deployer"] is True
        assert out["label"].startswith("Repeat deployer:")
        # No ClickHouse query was issued for the row path
        assert not any("FROM wallet_memory.deployer_profiles" in s for s in fake.executed)

    @pytest.mark.asyncio
    async def test_clickhouse_row_path(self) -> None:
        fake = FakeCHClient(profile_rows=[_profile_row()])
        with _mock_ch(fake), _no_redis():
            out = await get_deployer_profile(_DEPLOYER, "base")
        assert out is not None
        assert out["total_tokens_launched"] == 4
        assert out["rugged_pct"] == 0.25
        assert out["confidence"] == 0.8

    @pytest.mark.asyncio
    async def test_on_demand_compute_then_persist(self) -> None:
        """Miss on Redis + table → compute, cache to Redis, persist row."""
        fake = FakeCHClient(
            profile_rows=[],
            stats_rows=_stats_row(deployments=5, tokens=4),
            token_rows=[_token_row(_TOKEN_A, 30), _token_row(_TOKEN_B, 30)],
            lifecycle_rows=[
                _lifecycle_row(_TOKEN_A),
                _lifecycle_row(_TOKEN_B, vol_after_72h=200.0, swaps_after_72h=0),
            ],
        )
        with (
            _mock_ch(fake),
            patch("app.domains.scanner.engines.deployer_profiles.redis_get", AsyncMock(return_value=None)) as m_get,
            patch("app.domains.scanner.engines.deployer_profiles.redis_set", AsyncMock(return_value=True)) as m_set,
        ):
            out = await get_deployer_profile(_DEPLOYER, "base")

        assert out is not None
        assert out["is_repeat_deployer"] is True
        assert m_get.called
        assert m_set.called
        # Persisted to the table
        assert any("INSERT INTO wallet_memory.deployer_profiles" in s for s in fake.executed)

    @pytest.mark.asyncio
    async def test_non_repeat_returns_none(self) -> None:
        """Deployer with <=1 deployment has no profile."""
        fake = FakeCHClient(
            profile_rows=[],
            stats_rows=_stats_row(deployments=1, tokens=1),
            token_rows=[],
        )
        with _mock_ch(fake), _no_redis():
            out = await get_deployer_profile(_DEPLOYER, "base")
        assert out is None

    @pytest.mark.asyncio
    async def test_no_ch_returns_none(self) -> None:
        with patch("app.domains.scanner.engines.deployer_profiles._get_ch_client", return_value=None), _no_redis():
            out = await get_deployer_profile(_DEPLOYER, "base")
        assert out is None


# ═══════════════════════════════════════════════════════════════════════════
# BATCH REFRESH
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshProfiles:
    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_refresh_computes_and_persists(self) -> None:
        fake = FakeCHClient(
            repeat_rows=[_repeat_row(_DEPLOYER, deployments=5, tokens=4)],
            stats_rows=_stats_row(deployments=5, tokens=4),
            token_rows=[_token_row(_TOKEN_A, 30), _token_row(_TOKEN_B, 30)],
            lifecycle_rows=[
                _lifecycle_row(_TOKEN_A),
                _lifecycle_row(_TOKEN_B, vol_after_72h=200.0, swaps_after_72h=0),
            ],
        )
        with _mock_ch(fake), _no_redis():
            summary = await refresh_deployer_profiles(chain="base")

        assert summary["deployers_found"] == 1
        assert summary["profiles_computed"] == 1
        assert summary["stored"] == 1
        assert summary["errors"] == []
        assert any("INSERT INTO wallet_memory.deployer_profiles" in s for s in fake.executed)

    @pytest.mark.asyncio
    async def test_refresh_empty_when_no_repeat_deployers(self) -> None:
        fake = FakeCHClient(repeat_rows=[])
        with _mock_ch(fake), _no_redis():
            summary = await refresh_deployer_profiles()
        assert summary["deployers_found"] == 0
        assert summary["profiles_computed"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════


class TestConstants:
    def test_min_deployments_means_repeat(self) -> None:
        """A repeat deployer is any wallet with >1 deployment."""
        assert MIN_DEPLOYMENTS == 2
