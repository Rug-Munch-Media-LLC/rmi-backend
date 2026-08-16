"""Unit tests for own_wallet_map — first-20 buyer/holder role classification.

Covers ``get_own_wallet_map()`` with a fake ClickHouse client standing in for
the ``_funding_queries`` layer (``_query_top_holders`` / ``_query_holder_first_tx``
/ ``_query_first_funders``):

  - All six roles (Dev / Whale / Fish / Shrimp / Fresh / Bundle)
  - Wallet-birth (fresh) + funding-source (bundle) enrichment
  - Birdeye fallback when ClickHouse has no holder data
  - Fail-open "—" when holder / funding data is unreachable

The engine reaches ClickHouse through ``app.domains.scanner.engines._funding_queries``,
which binds ``_get_ch_client`` at module import — that name is the patch target.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.domains.scanner.engines.own_wallet_map import (
    ROLE_EMOJI,
    _classify_holders,
    clear_cache,
    get_cache_stats,
    get_own_wallet_map,
)

#: Role order used by the map-line builder (mirrors the engine's _ROLE_ORDER).
_ROLE_ORDER: tuple[str, ...] = ("dev", "whale", "fish", "shrimp", "fresh", "bundle")

_DEPLOYER = "0xdeployer"
_TOKEN = "0xtoken"
_CHAIN = "ethereum"


def _naive_dt(days_ago: float = 0.0) -> datetime:
    """Naive UTC datetime relative to now (matches engine's now_naive)."""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)


class FakeFundingClient:
    """Sync ClickHouse stand-in dispatching on wallet-map SQL fragments."""

    def __init__(
        self,
        holders: list[tuple] | None = None,
        first_tx: list[tuple] | None = None,
        funders: list[tuple] | None = None,
        first_buy: list[tuple] | None = None,
        wallet_funder: list[tuple] | None = None,
        funder_tx_counts: list[tuple] | None = None,
        raise_on: str | None = None,
    ) -> None:
        self.holders = holders or []
        self.first_tx = first_tx or []
        self.funders = funders or []
        self.first_buy = first_buy or []
        self.wallet_funder = wallet_funder or []
        self.funder_tx_counts = funder_tx_counts or []
        self.raise_on = raise_on
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if self.raise_on and self.raise_on in sql:
            raise TypeError(f"boom: {self.raise_on}")
        # Order matters: most specific fragments first.
        if "sum(cnt) AS total" in sql:  # _query_funder_tx_counts
            return self.funder_tx_counts
        if "to_address = %(addr)s" in sql and "AS funder" in sql:  # _query_wallet_first_funder
            return self.wallet_funder
        if "AS funder" in sql:  # _query_first_funders
            return self.funders
        if "AS first_buy_ts" in sql:  # _query_holder_first_buy
            return self.first_buy
        if "argMin(block_timestamp, block_timestamp) AS first_ts" in sql:  # first-tx (in/out)
            return self.first_tx
        if "holder_balances FINAL" in sql:  # _query_top_holders (EVM)
            return self.holders
        return []


def _mock_funding_ch(fake: FakeFundingClient):
    return patch("app.domains.scanner.engines._funding_queries._get_ch_client", return_value=fake)


def _no_birdeye():
    return patch("app.domains.scanner.engines.own_wallet_map._fetch_birdeye_holders", return_value=[])


def _holder_rows() -> list[tuple]:
    """(address, balance) rows — percentages relative to the top-8 total (131000)."""
    return [
        (_DEPLOYER, 100000.0),  # 76.3%  -> dev
        ("0xholder_b", 20000.0),  # 15.3%  -> whale
        ("0xholder_c", 4000.0),  # 3.05%  -> fresh (born 2d ago)
        ("0xholder_d", 3000.0),  # 2.29%  -> bundle (funder F1)
        ("0xholder_f", 2000.0),  # 1.53%  -> fish
        ("0xholder_e", 900.0),  # 0.69%  -> bundle (funder F1)
        ("0xholder_g", 700.0),  # 0.53%  -> shrimp
        ("0xholder_h", 400.0),  # 0.31%  -> shrimp
    ]


def _first_tx_rows() -> list[tuple]:
    """(holder, first_ts, first_token, first_tx_hash) — holder_c is fresh."""
    rows = []
    for addr in ("0xholder_b", "0xholder_d", "0xholder_f", "0xholder_e", "0xholder_g", "0xholder_h"):
        rows.append((addr, _naive_dt(30), "0xsometoken", "0xtx"))
    rows.append((_DEPLOYER, _naive_dt(60), "0xsometoken", "0xtx"))
    rows.append(("0xholder_c", _naive_dt(2), "0xsometoken", "0xtx"))
    return rows


def _funder_rows() -> list[tuple]:
    """(holder, funder, first_tx, first_ts) — D and E share funder F1."""
    rows = []
    for addr in ("0xholder_b", "0xholder_f", "0xholder_g", "0xholder_h"):
        rows.append((addr, f"0xfunder_{addr}", "0xtx", _naive_dt(40)))
    rows.append((_DEPLOYER, "0xfunder_dev", "0xtx", _naive_dt(70)))
    rows.append(("0xholder_c", "0xfunder_c", "0xtx", _naive_dt(2)))
    rows.append(("0xholder_d", "0xfunder1", "0xtx", _naive_dt(40)))
    rows.append(("0xholder_e", "0xfunder1", "0xtx", _naive_dt(40)))
    return rows


class TestGetOwnWalletMap:
    """Role classification driven end-to-end through get_own_wallet_map."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_classifies_all_six_roles(self) -> None:
        """Dev / Whale / Fish / Shrimp / Fresh / Bundle all assigned correctly."""
        fake = FakeFundingClient(
            holders=_holder_rows(),
            first_tx=_first_tx_rows(),
            funders=_funder_rows(),
        )
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)

        assert out["check"] == "wallet_map"
        assert out["result"] == "scanned"
        assert out["confidence"] == 0.8
        assert out["roles"] == {"dev": 1, "whale": 1, "fish": 1, "shrimp": 2, "fresh": 1, "bundle": 2}
        # Line is built from ROLE_EMOJI — note the engine renders "shrimp" with
        # U+1F964 (🥤) by design; asserting via the same constants keeps this
        # test immune to that pre-existing glyph quirk.
        assert out["wallet_map_line"] == " ".join(
            f"{ROLE_EMOJI[role]}{count}" for role, count in zip(_ROLE_ORDER, (1, 1, 1, 2, 1, 2), strict=True)
        )

        by_addr = {c["address"]: c for c in out["classified"]}
        assert len(by_addr) == 8
        assert by_addr[_DEPLOYER]["role"] == "dev"
        assert by_addr["0xholder_b"]["role"] == "whale"
        assert by_addr["0xholder_f"]["role"] == "fish"
        assert by_addr["0xholder_c"]["role"] == "fresh"  # born 2d ago, sub-whale
        assert by_addr["0xholder_d"]["role"] == "bundle"
        assert by_addr["0xholder_e"]["role"] == "bundle"
        assert by_addr["0xholder_g"]["role"] == "shrimp"
        assert by_addr["0xholder_h"]["role"] == "shrimp"
        # Percentages are relative to the tracked top-8 total.
        assert by_addr[_DEPLOYER]["percentage"] == pytest.approx(76.34, abs=0.01)
        # Fresh wallet carries its birth timestamp through.
        assert by_addr["0xholder_c"]["first_tx_ts"] is not None

    @pytest.mark.asyncio
    async def test_deployer_match_is_ci_case_insensitive(self) -> None:
        """Uppercase deployer input still matches the lowercased holder row."""
        fake = FakeFundingClient(holders=_holder_rows(), first_tx=_first_tx_rows(), funders=_funder_rows())
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER.upper())
        assert out["roles"]["dev"] == 1

    @pytest.mark.asyncio
    async def test_fail_open_when_no_clickhouse_and_no_birdeye(self) -> None:
        """Unreachable holder data -> 'unavailable' with a '—' map line."""
        with _mock_funding_ch(FakeFundingClient(holders=[])), _no_birdeye():
            out = await get_own_wallet_map(_TOKEN, _CHAIN)

        assert out["check"] == "wallet_map"
        assert out["result"] == "unavailable"
        assert out["wallet_map_line"] == "—"
        assert out["roles"] == {"dev": 0, "whale": 0, "fish": 0, "shrimp": 0, "fresh": 0, "bundle": 0}
        assert out["classified"] == []
        assert out["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_birdeye_fallback_used_when_clickhouse_empty(self) -> None:
        """No ClickHouse holder rows -> Birdeye distribution is classified instead."""
        birdeye_holders = [
            {"address": _DEPLOYER, "balance": 500.0, "percentage": 50.0},
            {"address": "0xbird_b", "balance": 300.0, "percentage": 30.0},
            {"address": "0xbird_c", "balance": 20.0, "percentage": 2.0},
        ]
        with (
            _mock_funding_ch(FakeFundingClient(holders=[])),
            patch(
                "app.domains.scanner.engines.own_wallet_map._fetch_birdeye_holders",
                return_value=birdeye_holders,
            ),
        ):
            out = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)

        assert out["result"] == "scanned"
        assert out["roles"]["dev"] == 1
        assert out["roles"]["whale"] == 1  # 30%
        assert out["roles"]["fish"] == 1  # 2% (no birth/funder data -> not fresh/bundle)
        assert out["roles"]["fresh"] == 0
        assert out["roles"]["bundle"] == 0

    @pytest.mark.asyncio
    async def test_enrichment_failure_degrades_without_fresh_or_bundle(self) -> None:
        """First-tx/funder queries blowing up -> no fresh/bundle, rest still classified."""
        fake = FakeFundingClient(
            holders=_holder_rows(),
            raise_on="argMin(block_timestamp, block_timestamp) AS first_ts",
        )
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)

        assert out["result"] == "scanned"
        assert out["roles"]["fresh"] == 0  # holder_c lost its birth timestamp
        assert out["roles"]["bundle"] == 0  # no funders resolved
        assert out["roles"]["dev"] == 1
        assert out["roles"]["whale"] == 1
        # Sub-whale holders without birth/funder data fall through to size:
        # c/d/f (1.5-3%) -> fish, e/g/h (<1%) -> shrimp.
        assert out["roles"]["fish"] == 3
        assert out["roles"]["shrimp"] == 3

    @pytest.mark.asyncio
    async def test_cache_serves_second_call(self) -> None:
        """Second call within TTL is served from cache (no extra CH queries)."""
        fake = FakeFundingClient(holders=_holder_rows(), first_tx=_first_tx_rows(), funders=_funder_rows())
        with _mock_funding_ch(fake):
            first = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)
            assert first["result"] == "scanned"
            queries_after_first = len(fake.executed)
            second = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)
        assert second["result"] == "scanned"
        assert len(fake.executed) == queries_after_first  # no new queries

    def test_cache_stats_shape(self) -> None:
        """get_cache_stats exposes the wallet-map cache contract."""
        stats = get_cache_stats()
        assert stats["cache_size"] == 0
        assert stats["cache_max"] == 100
        assert stats["cache_ttl_seconds"] == 300
        assert stats["max_holders"] == 20


class TestClassifyHolders:
    """Direct unit tests for the pure classification logic."""

    def test_precedence_dev_over_fresh_and_bundle(self) -> None:
        """Deployer is dev even when it is also fresh and shares a funder."""
        holders = [
            {"address": _DEPLOYER, "percentage": 1.5, "first_tx_ts": _naive_dt(1), "funder": "0xfunder1"},
            {"address": "0xb", "percentage": 1.0, "first_tx_ts": _naive_dt(20), "funder": "0xfunder1"},
        ]
        classified = _classify_holders(holders, deployer=_DEPLOYER)
        assert classified[0]["role"] == "dev"
        assert classified[1]["role"] == "bundle"

    def test_fresh_only_for_sub_whale(self) -> None:
        """A brand-new 6% wallet is still a whale, not fresh."""
        holders = [
            {"address": "0xa", "percentage": 6.0, "first_tx_ts": _naive_dt(1), "funder": "0xf"},
        ]
        classified = _classify_holders(holders)
        assert classified[0]["role"] == "whale"

    def test_bad_first_tx_never_fresh(self) -> None:
        """Garbage first_tx values fail open to non-fresh (no exception)."""
        holders = [
            {"address": "0xa", "percentage": 2.0, "first_tx_ts": "not-a-date", "funder": "0xf"},
        ]
        classified = _classify_holders(holders)
        assert classified[0]["role"] == "fish"


# ── CEX funding breakdown + deployer funding source ─────────────────────────
# Real internal entity_registry addresses (Binance 7 / Coinbase 1 / Tornado):
_BINANCE_7 = "0xBE0eB53FC46b790099138e3d32C721856d41e865"
_COINBASE_1 = "0x503828976D22510aad0201ac7EC88293211D23Da"
_TORNADO_01 = "0xA160cdAB225685dA1d56aa342Ad8841c3b53f291"


def _cex_holder_rows() -> list[tuple]:
    """8 holders — balances only drive roles; funding drives the breakdown."""
    return [(f"0xholder_{i}", 1000.0 - i) for i in range(8)]


def _cex_funder_rows() -> list[tuple]:
    """8 holders: 3x Binance, 1x Coinbase, 4x unknown."""
    rows = []
    for i in range(3):
        rows.append((f"0xholder_{i}", _BINANCE_7, "0xtx", _naive_dt(40)))
    rows.append(("0xholder_3", _COINBASE_1, "0xtx", _naive_dt(40)))
    for i in range(4, 8):
        rows.append((f"0xholder_{i}", f"0xfunder_{i}", "0xtx", _naive_dt(40)))
    return rows


class TestCexFundingBreakdown:
    """CEX-funding classification of holders' first-funders (requirement 1)."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_breakdown_and_line(self) -> None:
        """3 Binance + 1 Coinbase + 4 unknown funders → honest per-CEX %."""
        fake = FakeFundingClient(holders=_cex_holder_rows(), funders=_cex_funder_rows())
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN)

        assert out["result"] == "scanned"
        assert out["cex_funding"]["breakdown"] == {"Binance": 3, "Coinbase": 1}
        assert out["cex_funding"]["unknown_count"] == 4
        assert out["cex_funding"]["total_classified_funders"] == 8
        assert out["cex_funding_line"] == "🏦 CEX funding: Binance 38% · Coinbase 12% · unknown 50%"

    @pytest.mark.asyncio
    async def test_unknown_only(self) -> None:
        """No CEX funders → 100% unknown bucket (honest, not fabricated CEXs)."""
        fake = FakeFundingClient(holders=_cex_holder_rows(), funders=_cex_funder_rows())
        fake.funders = [(f"0xholder_{i}", f"0xotc_{i}", "0xtx", _naive_dt(40)) for i in range(8)]
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN)

        assert out["cex_funding"]["breakdown"] == {}
        assert out["cex_funding_line"] == "🏦 CEX funding: unknown 100%"

    @pytest.mark.asyncio
    async def test_fail_open_to_dash_when_no_funders(self) -> None:
        """No funder rows → both funding lines are honest '—'."""
        fake = FakeFundingClient(holders=_cex_holder_rows())
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN)

        assert out["result"] == "scanned"
        assert out["cex_funding_line"] == "—"
        assert out["deployer_funding_line"] == "—"
        assert out["deployer_funding"] == {}


class TestDeployerFunding:
    """Deployer funding source: CEX vs mixer vs fresh vs unknown (requirement 2)."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_deployer_funded_by_cex_when_holder(self) -> None:
        """Deployer's funder is a Binance hot wallet (reused from holder list)."""
        rows = [(f"0xholder_{i}", 1000.0 - i) for i in range(4)]
        rows.append((_DEPLOYER, 5000.0))  # dev holder
        funders = [(f"0xholder_{i}", f"0xfunder_{i}", "0xtx", _naive_dt(40)) for i in range(4)]
        funders.append((_DEPLOYER, _BINANCE_7, "0xtx", _naive_dt(70)))
        fake = FakeFundingClient(holders=rows, funders=funders)
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)

        assert out["deployer_funding"] == {"source": "cex", "label": "Binance"}
        assert out["deployer_funding_line"] == "🏦 Deployer funded by: Binance"

    @pytest.mark.asyncio
    async def test_deployer_funded_by_cex_when_not_holder(self) -> None:
        """Deployer not among top holders → first-funder queried directly."""
        fake = FakeFundingClient(
            holders=_cex_holder_rows(),
            wallet_funder=[(_BINANCE_7,)],
        )
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)

        assert out["deployer_funding"]["source"] == "cex"
        assert out["deployer_funding_line"] == "🏦 Deployer funded by: Binance"

    @pytest.mark.asyncio
    async def test_deployer_funded_by_mixer(self) -> None:
        """Deployer funded from Tornado Cash → mixer classification."""
        rows = [(f"0xholder_{i}", 1000.0 - i) for i in range(4)]
        rows.append((_DEPLOYER, 5000.0))
        funders = [(f"0xholder_{i}", f"0xfunder_{i}", "0xtx", _naive_dt(40)) for i in range(4)]
        funders.append((_DEPLOYER, _TORNADO_01, "0xtx", _naive_dt(70)))
        fake = FakeFundingClient(holders=rows, funders=funders)
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)

        assert out["deployer_funding"]["source"] == "mixer"
        assert out["deployer_funding_line"].startswith("🌀 Deployer funded by: mixer")

    @pytest.mark.asyncio
    async def test_deployer_funded_by_fresh_wallet(self) -> None:
        """Unknown funder with <10 total transfers → fresh wallet."""
        rows = [(f"0xholder_{i}", 1000.0 - i) for i in range(4)]
        rows.append((_DEPLOYER, 5000.0))
        funders = [(f"0xholder_{i}", f"0xfunder_{i}", "0xtx", _naive_dt(40)) for i in range(4)]
        funders.append((_DEPLOYER, "0xfunder_dev", "0xtx", _naive_dt(70)))
        fake = FakeFundingClient(
            holders=rows,
            funders=funders,
            funder_tx_counts=[("0xfunder_dev", 3)],
        )
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)

        assert out["deployer_funding"]["source"] == "fresh"
        assert out["deployer_funding_line"] == "🌱 Deployer funded by: fresh wallet"

    @pytest.mark.asyncio
    async def test_deployer_funded_by_unknown_wallet(self) -> None:
        """No CEX/mixer match + not fresh → honest unknown bucket."""
        rows = [(f"0xholder_{i}", 1000.0 - i) for i in range(4)]
        rows.append((_DEPLOYER, 5000.0))
        funders = [(f"0xholder_{i}", f"0xfunder_{i}", "0xtx", _naive_dt(40)) for i in range(4)]
        funders.append((_DEPLOYER, "0xfunder_dev", "0xtx", _naive_dt(70)))
        fake = FakeFundingClient(
            holders=rows,
            funders=funders,
            funder_tx_counts=[("0xfunder_dev", 42)],
        )
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN, deployer=_DEPLOYER)

        assert out["deployer_funding"]["source"] == "unknown"
        assert out["deployer_funding_line"] == "🤷 Deployer funded by: unknown wallet"

    @pytest.mark.asyncio
    async def test_fail_open_when_no_deployer(self) -> None:
        """No deployer provided → deployer funding stays honest '—'."""
        fake = FakeFundingClient(holders=_cex_holder_rows(), funders=_cex_funder_rows())
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN)

        assert out["deployer_funding"] == {}
        assert out["deployer_funding_line"] == "—"


# ── Buyer timing: fresh-wallet % + recently-funded % ─────────────────────────


def _timing_holder_rows() -> list[tuple]:
    """(address, balance) — small balances so roles stay sub-whale (timing is
    computed over the raw holder dicts, roles are irrelevant to it)."""
    return [
        ("0xfresh_a", 5.0),  # born 2h before buy -> FRESH, funded 1h before -> RECENTLY FUNDED
        ("0xfresh_b", 4.0),  # born 5h before buy -> FRESH, funded 20h before -> not recently funded
        ("0xold_c", 3.0),  # born 30d before buy -> not fresh, funded 30m before -> RECENTLY FUNDED
        ("0xold_d", 2.0),  # born 30d before buy -> not fresh, funded 3d before -> neither
    ]


def _timing_first_tx_rows() -> list[tuple]:
    """(holder, first_ts, first_token, first_tx_hash) — wallet birth times."""
    now = _naive_dt(0)
    return [
        ("0xfresh_a", now - timedelta(hours=2), "0xsometoken", "0xtx"),
        ("0xfresh_b", now - timedelta(hours=5), "0xsometoken", "0xtx"),
        ("0xold_c", now - timedelta(days=30), "0xsometoken", "0xtx"),
        ("0xold_d", now - timedelta(days=30), "0xsometoken", "0xtx"),
    ]


def _timing_funder_rows() -> list[tuple]:
    """(holder, funder, first_tx, first_ts) — funding event times."""
    now = _naive_dt(0)
    return [
        ("0xfresh_a", "0xfunder_a", "0xtx", now - timedelta(hours=1)),
        ("0xfresh_b", "0xfunder_b", "0xtx", now - timedelta(hours=20)),
        ("0xold_c", "0xfunder_c", "0xtx", now - timedelta(minutes=30)),
        ("0xold_d", "0xfunder_d", "0xtx", now - timedelta(days=3)),
    ]


def _timing_first_buy_rows() -> list[tuple]:
    """(holder, first_buy_ts) — everyone buys ~now."""
    now = _naive_dt(0)
    return [(addr, now) for addr, _ in _timing_holder_rows()]


class TestBuyerTiming:
    """Fresh-wallet % + recently-funded % surfaced on the wallet map result."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_fresh_and_recently_funded_percentages(self) -> None:
        """2/4 fresh (born <24h before buy) and 2/4 funded (<6h before buy)."""
        fake = FakeFundingClient(
            holders=_timing_holder_rows(),
            first_tx=_timing_first_tx_rows(),
            funders=_timing_funder_rows(),
            first_buy=_timing_first_buy_rows(),
        )
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN)

        assert out["result"] == "scanned"
        timing = out["buyer_timing"]
        assert timing["result"] == "scanned"
        assert timing["fresh_wallet_pct"] == 50
        assert timing["fresh_wallet_count"] == 2
        assert timing["fresh_wallet_denominator"] == 4
        assert timing["recently_funded_pct"] == 50
        assert timing["recently_funded_count"] == 2
        assert timing["recently_funded_denominator"] == 4
        assert out["fresh_wallet_line"] == "🌱 Fresh wallets: 50% (created <24h before buy)"
        assert out["recently_funded_line"] == "💸 Recently funded: 50% (funded <6h before buy)"

        by_addr = {e["address"]: e for e in timing["breakdown"]}
        assert by_addr["0xfresh_a"]["fresh_pre_buy"] is True
        assert by_addr["0xfresh_b"]["fresh_pre_buy"] is True
        assert by_addr["0xold_c"]["fresh_pre_buy"] is False
        assert by_addr["0xold_c"]["recently_funded"] is True
        assert by_addr["0xold_d"]["recently_funded"] is False

    @pytest.mark.asyncio
    async def test_fail_open_dash_when_no_buy_timestamps(self) -> None:
        """No first-buy rows -> percentages absent, lines render '—' (honest)."""
        fake = FakeFundingClient(
            holders=_timing_holder_rows(),
            first_tx=_timing_first_tx_rows(),
            funders=_timing_funder_rows(),
            first_buy=[],
        )
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN)

        assert out["result"] == "scanned"  # roles still classify fine
        timing = out["buyer_timing"]
        assert timing["result"] == "unavailable"
        assert timing["fresh_wallet_pct"] is None
        assert timing["recently_funded_pct"] is None
        assert out["fresh_wallet_line"] == "—"
        assert out["recently_funded_line"] == "—"

    @pytest.mark.asyncio
    async def test_denominators_skip_holders_missing_a_timestamp(self) -> None:
        """Holders without funding rows contribute to fresh but not funded."""
        fake = FakeFundingClient(
            holders=_timing_holder_rows(),
            first_tx=_timing_first_tx_rows(),
            funders=_timing_funder_rows()[:1],  # only fresh_a has a funder row
            first_buy=_timing_first_buy_rows(),
        )
        with _mock_funding_ch(fake):
            out = await get_own_wallet_map(_TOKEN, _CHAIN)

        timing = out["buyer_timing"]
        # Fresh: all four have birth + buy -> 2/4 = 50%.
        assert timing["fresh_wallet_pct"] == 50
        assert timing["fresh_wallet_denominator"] == 4
        # Funded: only fresh_a has a funding timestamp -> 1/1 = 100%.
        assert timing["recently_funded_pct"] == 100
        assert timing["recently_funded_denominator"] == 1

    def test_pure_compute_with_garbage_dates_fail_open(self) -> None:
        """Garbage timestamps are skipped, never crash the breakdown."""
        from app.domains.scanner.engines.own_wallet_map import _compute_buyer_timing

        timing = _compute_buyer_timing(
            [
                {"address": "0xa", "first_tx_ts": "not-a-date", "funding_ts": None, "buy_ts": _naive_dt(0)},
                {"address": "0xb", "first_tx_ts": None, "funding_ts": "garbage", "buy_ts": None},
            ]
        )
        assert timing["result"] == "unavailable"
        assert timing["fresh_wallet_pct"] is None
        assert timing["recently_funded_pct"] is None
