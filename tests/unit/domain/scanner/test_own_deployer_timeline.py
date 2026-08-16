"""Unit tests for own_deployer_timeline — prior-token history for the deep scan.

Covers ``get_own_deployer_timeline()`` with a fake ClickHouse client standing
in for the ``_deployer_fingerprint_queries`` layer plus the engine's own
balance query:

  - prior-token + rugged count (7-day maturity proxy)
  - funded-from classification (CEX / wallet)
  - deployer age + native balance
  - fail-open "—" / "unknown" paths (no client, query exception, empty address)

Patch points: the fingerprint queries resolve ``_get_ch_client`` lazily through
``deployer_fingerprint``, while the balance query imports it from
``lite_chain_indexer`` inside the function body — both names are patched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.domains.scanner.engines.own_deployer_timeline import (
    _build_timeline_line,
    clear_cache,
    get_cache_stats,
    get_own_deployer_timeline,
)

_DEPLOYER = "0xdeployer"
_CHAIN = "ethereum"


def _naive_dt(days_ago: float = 0.0) -> datetime:
    """Naive UTC datetime relative to now (matches engine's now_naive)."""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)


class FakeTimelineClient:
    """Sync ClickHouse stand-in dispatching on deployer-timeline SQL fragments."""

    def __init__(
        self,
        stats: list[tuple] | None = None,
        tokens: list[tuple] | None = None,
        dex: list[tuple] | None = None,
        funder_native: list[tuple] | None = None,
        funder_fallback: list[tuple] | None = None,
        funder_labels: list[tuple] | None = None,
        balance: list[tuple] | None = None,
        raise_on: str | None = None,
    ) -> None:
        self.stats = stats
        self.tokens = tokens or []
        self.dex = dex or []
        self.funder_native = funder_native
        self.funder_fallback = funder_fallback
        self.funder_labels = funder_labels or []
        self.balance = balance
        self.raise_on = raise_on
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if self.raise_on and self.raise_on in sql:
            raise TypeError(f"boom: {self.raise_on}")
        # Order matters: most specific fragments first.
        if "count() AS total_deployments" in sql:  # _query_deployer_stats
            return self.stats or []
        if "first_dex_ts" in sql:  # _query_token_dex_stats
            return self.dex
        if "wallet_memory.deployer_events" in sql and "GROUP BY token_address" in sql:  # per-token
            return self.tokens
        if "sum(net)" in sql:  # _query_deployer_balance (EVM) — also contains the native marker clause
            return self.balance or []
        if "solana_spl_balance_changes" in sql:  # _query_deployer_balance (solana)
            return self.balance or []
        if "lower(token_address) IN" in sql:  # native first-incoming funder
            return self.funder_native or []
        if "GROUP BY from_address" in sql:  # any first-incoming funder (fallback)
            return self.funder_fallback or []
        if "wallet_memory.wallet_labels" in sql:  # _query_funder_label
            return self.funder_labels
        return []


def _mock_fingerprint_ch(fake: FakeTimelineClient):
    return patch(
        "app.domains.scanner.engines.deployer_fingerprint._get_ch_client",
        return_value=fake,
    )


def _mock_balance_ch(fake: FakeTimelineClient):
    return patch(
        "app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client",
        return_value=fake,
    )


def _stats_row(total: int, first_ts: datetime) -> list[tuple]:
    """Deployer stats aggregate (total, tokens, chains, first_ts, last_ts)."""
    return [(total, total, ["ethereum"], first_ts, first_ts)]


def _token_row(addr: str, deployed_ts: datetime) -> tuple:
    return (addr, "ethereum", deployed_ts, "0xdeploy")


def _dex_row(addr: str, last_swap_ts: datetime | None) -> tuple:
    return (addr, last_swap_ts, last_swap_ts, 10)


def _cex_funder() -> list[tuple]:
    """Native first-incoming funder (is_native=True)."""
    return [("0xcexfund", _naive_dt(40), "0xfundtx", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")]


def _cex_labels() -> list[tuple]:
    """wallet_labels row marking the funder as an exchange."""
    return [("0xcexfund", "ethereum", "Binance", "exchange", "hot", "manual")]


def _full_fake() -> FakeTimelineClient:
    """Happy-path fixture: 3 prior tokens, 1 rugged, CEX-funded, age 14d, bal 42."""
    return FakeTimelineClient(
        stats=_stats_row(3, _naive_dt(14)),
        tokens=[
            _token_row("0xaaa1", _naive_dt(30)),  # matured, no swaps after day 23 -> rugged
            _token_row("0xaaa2", _naive_dt(20)),  # matured, survived past day 13
            _token_row("0xaaa3", _naive_dt(2)),  # too young to judge
        ],
        dex=[
            _dex_row("0xaaa1", _naive_dt(25)),  # last swap before the 7-day boundary
            _dex_row("0xaaa2", _naive_dt(12)),  # last swap after the 7-day boundary
        ],
        funder_native=_cex_funder(),
        funder_labels=_cex_labels(),
        balance=[(42.0,)],
    )


class TestGetOwnDeployerTimeline:
    """Timeline assembly driven end-to-end through get_own_deployer_timeline."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_full_timeline(self) -> None:
        """Prior tokens, rugged count, CEX funding, age and balance all resolved."""
        fake = _full_fake()
        with _mock_fingerprint_ch(fake), _mock_balance_ch(fake):
            out = await get_own_deployer_timeline(_DEPLOYER, _CHAIN)

        assert out["check"] == "deployer_timeline"
        assert out["result"] == "scanned"
        assert out["prior_tokens"] == 3
        assert out["rugged_count"] == 1  # t1 matured with no post-day-7 swaps; t2 survived
        assert out["funded_from"] == "CEX"
        assert out["deployer_age_days"] == 14
        assert out["deployer_balance"] == "42"
        assert out["confidence"] == 0.7
        assert out["deployer_timeline_line"] == (
            "👨‍💻 Deployer: 3 prior tokens · 1 rugged · funded from CEX · age 14d · bal 42"
        )

    @pytest.mark.asyncio
    async def test_fresh_wallet_funded_from_wallet(self) -> None:
        """Native funder with no CEX label -> 'wallet' (not 'unknown')."""
        fake = FakeTimelineClient(
            stats=_stats_row(1, _naive_dt(4)),
            tokens=[_token_row("0xaaa1", _naive_dt(4))],
            dex=[],
            funder_native=[("0xpersonalfund", _naive_dt(5), "0xfundtx", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")],
            funder_labels=[],
            balance=[(0.0,)],
        )
        with _mock_fingerprint_ch(fake), _mock_balance_ch(fake):
            out = await get_own_deployer_timeline(_DEPLOYER, _CHAIN)

        assert out["result"] == "scanned"
        assert out["funded_from"] == "wallet"
        assert out["prior_tokens"] == 1
        assert out["rugged_count"] == 0  # only token is too young
        assert out["deployer_balance"] == "0"
        assert "funded from wallet" in out["deployer_timeline_line"]

    @pytest.mark.asyncio
    async def test_fail_open_when_no_client(self) -> None:
        """Unreachable ClickHouse -> unknown components, '—' in the line."""
        with _mock_fingerprint_ch(FakeTimelineClient()), _mock_balance_ch(None):
            out = await get_own_deployer_timeline(_DEPLOYER, _CHAIN)

        assert out["prior_tokens"] == "unknown"
        assert out["rugged_count"] == "unknown"
        assert out["funded_from"] == "unknown"
        assert out["deployer_age_days"] is None
        assert out["deployer_balance"] == "unknown"
        assert out["confidence"] == 0.3
        assert out["deployer_timeline_line"] == (
            "👨‍💻 Deployer: — prior tokens · — rugged · funded from — · age — · bal —"
        )

    @pytest.mark.asyncio
    async def test_query_exception_yields_unknown_line(self) -> None:
        """A query raising a non-graceful error -> 'Deployer: unknown' line."""
        fake = FakeTimelineClient(raise_on="count() AS total_deployments")
        with _mock_fingerprint_ch(fake), _mock_balance_ch(fake):
            out = await get_own_deployer_timeline(_DEPLOYER, _CHAIN)

        assert out["result"] == "unavailable"
        assert out["prior_tokens"] == "unknown"
        assert out["deployer_timeline_line"] == "👨‍💻 Deployer: unknown"

    @pytest.mark.asyncio
    async def test_empty_deployer_address(self) -> None:
        """No deployer -> fail-open line without any ClickHouse access."""
        with _mock_fingerprint_ch(FakeTimelineClient()), _mock_balance_ch(FakeTimelineClient()):
            out = await get_own_deployer_timeline("", _CHAIN)

        assert out["result"] == "unavailable"
        assert out["deployer"] == ""
        assert out["deployer_timeline_line"] == "👨‍💻 Deployer: —"

    @pytest.mark.asyncio
    async def test_cache_serves_second_call(self) -> None:
        """Second call within TTL is served from cache (no extra CH queries)."""
        fake = _full_fake()
        with _mock_fingerprint_ch(fake), _mock_balance_ch(fake):
            first = await get_own_deployer_timeline(_DEPLOYER, _CHAIN)
            assert first["result"] == "scanned"
            queries_after_first = len(fake.executed)
            second = await get_own_deployer_timeline(_DEPLOYER, _CHAIN)
        assert second["result"] == "scanned"
        assert len(fake.executed) == queries_after_first  # no new queries

    def test_cache_stats_shape(self) -> None:
        """get_cache_stats exposes the timeline cache contract."""
        stats = get_cache_stats()
        assert stats["cache_size"] == 0
        assert stats["cache_max"] == 200
        assert stats["cache_ttl_seconds"] == 600
        assert stats["maturity_days"] == 7


class TestBuildTimelineLine:
    """Direct unit tests for the line builder."""

    def test_full_line(self) -> None:
        assert _build_timeline_line(3, 1, "CEX", 14, "42") == (
            "👨‍💻 Deployer: 3 prior tokens · 1 rugged · funded from CEX · age 14d · bal 42"
        )

    def test_unknown_components_render_dashes(self) -> None:
        assert _build_timeline_line("unknown", "unknown", "unknown", None, "unknown") == (
            "👨‍💻 Deployer: — prior tokens · — rugged · funded from — · age — · bal —"
        )
