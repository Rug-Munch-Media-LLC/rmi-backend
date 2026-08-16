"""Unit tests for own_wallet_trust — 0-100 wallet trust score + verdict.

Covers ``get_wallet_trust()`` with a fake ClickHouse client standing in
for the wallet-stats / first-funder / deployer-history / label queries:

  - Aged CEX-funded clean wallet → 🟢 high trust
  - Fresh mixer-funded rugger → 🔴 low trust
  - Exchange hot wallet → special-cased Trust 90
  - No data at all → fail-open "—"
  - Caller context fallback when ClickHouse has nothing
  - Intel penalties (wash-trading / sniper) and win-rate scoring

The engine reaches ClickHouse through ``_funding_queries._get_ch_client``
(bound at import) and ``lite_chain_indexer._get_ch_client`` (imported
inside the deployer/label helpers) — both are patch targets.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.domains.scanner.engines.own_wallet_trust import (
    clear_cache,
    get_cache_stats,
    get_wallet_trust,
)

# Real entity-registry addresses used by the funding classifier.
_BINANCE_HOT = "0xBE0eB53FC46b790099138e3d32C721856d41e865"
_TORNADO = "0xA160cdAB225685dA1d56aa342Ad8841c3b53f291"

_CHAIN = "ethereum"


def _naive_dt(days_ago: float = 0.0) -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)


class FakeTrustClient:
    """Sync ClickHouse stand-in dispatching on trust-engine SQL fragments."""

    def __init__(
        self,
        stats: list[tuple] | None = None,
        funder: list[tuple] | None = None,
        rug_stats: list[tuple] | None = None,
        labels: list[tuple] | None = None,
    ) -> None:
        self.stats = stats or []
        self.funder = funder or []
        self.rug_stats = rug_stats or []
        self.labels = labels or []
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if "sum(is_scam_related)" in sql:  # _query_deployer_scam_stats
            return self.rug_stats
        if "FROM wallet_memory.wallet_labels" in sql:  # _query_batch_labels
            return self.labels
        if "AS first_seen" in sql and "count() AS total" in sql:  # _query_wallet_stats
            return self.stats
        if "AS funder" in sql and "to_address = %(addr)s" in sql:  # _query_wallet_first_funder
            return self.funder
        return []


def _mock_ch(fake: FakeTrustClient):
    return [
        patch("app.domains.scanner.engines._funding_queries._get_ch_client", return_value=fake),
        patch("app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client", return_value=fake),
    ]


@pytest.fixture(autouse=True)
def _clear_trust_cache():
    clear_cache()
    yield
    clear_cache()


async def _run_trust(wallet: str, fake: FakeTrustClient, **kwargs) -> dict:
    with _mock_ch(fake)[0], _mock_ch(fake)[1]:
        return await get_wallet_trust(wallet, _CHAIN, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════


class TestTrustScoring:
    async def test_aged_cex_funded_clean_wallet_high_trust(self) -> None:
        fake = FakeTrustClient(
            stats=[(_naive_dt(812), 4521)],
            funder=[(_BINANCE_HOT,)],
            rug_stats=[(4, 0)],
        )
        result = await _run_trust("0xagedwallet", fake)
        assert result["result"] == "scanned"
        assert result["trust_score"] >= 70
        assert result["trust_level"] == "high"
        assert result["trust_line"].startswith("\U0001f7e2 Trust")
        assert "aged" in result["signals"]
        assert any(s.startswith("CEX-funded") for s in result["signals"])
        assert "no prior rugs" in result["signals"]
        assert result["factors"]["age_days"] == 812
        assert result["factors"]["funding_source"] == "cex"
        assert result["factors"]["rug_count"] == 0

    async def test_fresh_mixer_funded_rugger_low_trust(self) -> None:
        fake = FakeTrustClient(
            stats=[(_naive_dt(2), 5)],
            funder=[(_TORNADO,)],
            rug_stats=[(5, 3)],
        )
        result = await _run_trust("0xrugger", fake)
        assert result["result"] == "scanned"
        assert result["trust_score"] < 40
        assert result["trust_level"] == "low"
        assert result["trust_line"].startswith("\U0001f534 Trust")
        assert "fresh" in result["signals"]
        assert "mixer-funded" in result["signals"]
        assert "linked to 3 rugs" in result["signals"]

    async def test_no_data_fails_open_to_dash(self) -> None:
        fake = FakeTrustClient()
        result = await _run_trust("0xghost", fake)
        assert result["result"] == "unavailable"
        assert result["trust_score"] is None
        assert result["trust_line"] == "\u2014"

    async def test_context_fallback_when_clickhouse_empty(self) -> None:
        fake = FakeTrustClient()
        ctx = {"wallet_age_days": 800, "tx_count": 2000, "funding_source": "binance"}
        result = await _run_trust("0xctxwallet", fake, context=ctx)
        assert result["result"] == "scanned"
        # Honest scoring: age (20) + activity (15) + CEX funding (25) = 60.
        # No deployer history resolved → rug component honestly absent.
        assert result["trust_score"] == 60
        assert "aged" in result["signals"]
        assert any(s.startswith("CEX-funded") for s in result["signals"])
        assert result["factors"]["funding_source"] == "cex"

    async def test_context_zero_age_is_ignored(self) -> None:
        # v2->legacy converters hardcode wallet_age_days=0 — must not
        # score the wallet as "born today".
        fake = FakeTrustClient(rug_stats=[(1, 0)])
        ctx = {"wallet_age_days": 0, "tx_count": 0, "funding_source": None}
        result = await _run_trust("0xunknownage", fake, context=ctx)
        assert result["result"] == "scanned"
        assert result["factors"].get("age_days") is None
        assert "fresh" not in result["signals"]

    async def test_intel_winrate_and_wash_penalty(self) -> None:
        fake = FakeTrustClient(stats=[(_naive_dt(400), 500)], funder=[(_BINANCE_HOT,)])
        intel = {"win_rate": 0.7, "wash_trading_score": 80.0, "sniper_score": 0.0}
        result = await _run_trust("0xwashy", fake, intel=intel)
        assert result["result"] == "scanned"
        assert "win-rate 70%" in result["signals"]
        assert "wash-trading" in result["signals"]
        assert result["factors"].get("wash_trading") is True
        # wash penalty lowers the score vs the same wallet without intel
        clean = await _run_trust("0xwashy", fake)
        assert result["trust_score"] < clean["trust_score"]


class TestExchangeSpecialCase:
    async def test_cex_hot_wallet_trust_90(self) -> None:
        with patch(
            "app.domains.scanner.engines.own_wallet_trust._fetch_own_labels",
            return_value={"entity_type": "cex_hot_wallet", "entity_name": "Binance"},
        ):
            result = await get_wallet_trust(_BINANCE_HOT, _CHAIN)
        assert result["result"] == "scanned"
        assert result["trust_score"] == 90
        assert result["trust_line"] == "\U0001f7e2 Trust 90/100 \u2014 exchange wallet"


class TestSanctions:
    async def test_sanctioned_wallet_penalized(self) -> None:
        fake = FakeTrustClient(
            stats=[(_naive_dt(900), 3000)],
            funder=[(_BINANCE_HOT,)],
            labels=[("0xsanctioned", "OFAC", "sanctioned", "", "trm", 1)],
        )
        with patch(
            "app.domains.scanner.engines.own_wallet_trust._fetch_own_labels",
            return_value={"is_sanctioned": True},
        ):
            result = await _run_trust("0xsanctioned", fake)
        assert result["result"] == "scanned"
        assert "sanctioned" in result["signals"]
        assert result["factors"].get("sanctioned") is True
        # sanctions should drag an otherwise-aged+CEX wallet down
        assert result["trust_score"] < 70


class TestCache:
    async def test_cache_stats_and_clear(self) -> None:
        fake = FakeTrustClient(stats=[(_naive_dt(100), 100)])
        await _run_trust("0xcacheme", fake)
        stats = get_cache_stats()
        assert stats["cache_size"] >= 1
        clear_cache()
        assert get_cache_stats()["cache_size"] == 0
