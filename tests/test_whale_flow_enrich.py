"""Tests for whale/smart-money flow enrichment (app/domains/scanner/whale_flow_enrich.py).

Pure unit tests — fetchers are injected as async callables; no network, no DB.
Covers:
  * whale_flow composes net-flow / tx-count / smart-money direction / evidence
  * fail-open: fetcher errors yield None fields (never raises)
  * whale_signal renders the right string / None for every branch
  * priority: a raw whale sell outranks a smart-money buy
  * the GMGN direction parser (bought / sold / flat / buy_pressure fallback)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.whale_flow_enrich import (
    _chain_candidates,
    _compose,
    _smart_money_direction,
    _to_float,
    fetch_smart_money_gmgn,
    fetch_whale_flow_clickhouse,
    whale_flow,
    whale_signal,
)

TOKEN = "0x1234567890abcdef1234567890abcdef12345678"
CHAIN = "solana"


def _whale_fetcher(
    *,
    net_flow: float | None = None,
    whale_tx_count: int | None = None,
    evidence: str | None = None,
) -> AsyncMock:
    return AsyncMock(return_value={"net_flow": net_flow, "whale_tx_count": whale_tx_count, "evidence": evidence})


def _smart_fetcher(
    *,
    bought: bool | None = None,
    sold: bool | None = None,
    evidence: str | None = None,
) -> AsyncMock:
    return AsyncMock(return_value={"bought": bought, "sold": sold, "evidence": evidence})


def _empty_whale() -> AsyncMock:
    return _whale_fetcher()


def _empty_smart() -> AsyncMock:
    return _smart_fetcher()


# ── whale_flow composition ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_whale_flow_net_selling_composes_full_shape():
    result = await whale_flow(
        TOKEN,
        CHAIN,
        whale_fetcher=_whale_fetcher(net_flow=-50_000.0, whale_tx_count=3, evidence="3 whale transfers"),
        smart_money_fetcher=_empty_smart(),
    )

    assert result == {
        "net_flow": pytest.approx(-50_000.0),
        "whale_tx_count": 3,
        "smart_money_bought": None,
        "smart_money_sold": None,
        "evidence": "3 whale transfers",
    }
    assert whale_signal(result) == "whales net selling"


@pytest.mark.asyncio
async def test_whale_flow_smart_money_bought_composes_full_shape():
    result = await whale_flow(
        TOKEN,
        CHAIN,
        whale_fetcher=_empty_whale(),
        smart_money_fetcher=_smart_fetcher(bought=True, sold=False, evidence="smart money net +12 (7d)"),
    )

    assert result["smart_money_bought"] is True
    assert result["smart_money_sold"] is False
    assert result["evidence"] == "smart money net +12 (7d)"
    assert whale_signal(result) == "smart money bought in"


@pytest.mark.asyncio
async def test_whale_flow_merges_evidence_from_both_fetchers():
    result = await whale_flow(
        TOKEN,
        CHAIN,
        whale_fetcher=_whale_fetcher(net_flow=-1.0, whale_tx_count=1, evidence="whale side"),
        smart_money_fetcher=_smart_fetcher(bought=True, sold=False, evidence="smart side"),
    )

    assert result["evidence"] == "whale side | smart side"


@pytest.mark.asyncio
async def test_whale_flow_passes_address_and_chain_to_fetchers():
    wf = _whale_fetcher()
    smf = _smart_fetcher()

    await whale_flow(TOKEN, CHAIN, whale_fetcher=wf, smart_money_fetcher=smf)

    wf.assert_awaited_once_with(TOKEN, CHAIN)
    smf.assert_awaited_once_with(TOKEN, CHAIN)


@pytest.mark.asyncio
async def test_whale_flow_partial_failure_keeps_other_signal():
    async def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("gmgn down")

    result = await whale_flow(
        TOKEN,
        CHAIN,
        whale_fetcher=_whale_fetcher(net_flow=-1.0, whale_tx_count=2, evidence="counted"),
        smart_money_fetcher=_boom,  # type: ignore[arg-type]
    )

    assert result["net_flow"] == pytest.approx(-1.0)
    assert result["whale_tx_count"] == 2
    assert result["smart_money_bought"] is None
    assert result["smart_money_sold"] is None
    assert whale_signal(result) == "whales net selling"


@pytest.mark.asyncio
async def test_whale_flow_fail_open_when_all_fetchers_raise():
    async def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("down")

    result = await whale_flow(TOKEN, CHAIN, whale_fetcher=_boom, smart_money_fetcher=_boom)  # type: ignore[arg-type]

    assert result["net_flow"] is None
    assert result["whale_tx_count"] is None
    assert result["smart_money_bought"] is None
    assert result["smart_money_sold"] is None
    assert whale_signal(result) is None


@pytest.mark.asyncio
async def test_whale_flow_empty_address_never_calls_fetchers():
    wf = _whale_fetcher(net_flow=-1.0)
    smf = _smart_fetcher(bought=True)

    result = await whale_flow("", CHAIN, whale_fetcher=wf, smart_money_fetcher=smf)

    assert result["net_flow"] is None
    assert result["smart_money_bought"] is None
    wf.assert_not_awaited()
    smf.assert_not_awaited()


@pytest.mark.asyncio
async def test_whale_flow_uses_default_fetchers_when_not_injected():
    with (
        patch(
            "app.domains.scanner.whale_flow_enrich.fetch_whale_flow_clickhouse",
            new=AsyncMock(
                return_value={"net_flow": None, "whale_tx_count": 0, "evidence": "no whale transfers in window"}
            ),
        ) as wf_default,
        patch(
            "app.domains.scanner.whale_flow_enrich.fetch_smart_money_gmgn",
            new=AsyncMock(return_value={"bought": None, "sold": None, "evidence": ""}),
        ) as sm_default,
    ):
        result = await whale_flow(TOKEN, CHAIN)

    wf_default.assert_awaited_once_with(TOKEN, CHAIN)
    sm_default.assert_awaited_once_with(TOKEN, CHAIN)
    assert result["whale_tx_count"] == 0
    assert result["evidence"] == "no whale transfers in window"


# ── whale_signal renderer ─────────────────────────────────────────────────


def test_whale_signal_smart_money_bought_is_bullish():
    assert (
        whale_signal({"net_flow": None, "smart_money_bought": True, "smart_money_sold": False})
        == "smart money bought in"
    )


def test_whale_signal_smart_money_sold_is_exiting():
    assert (
        whale_signal({"net_flow": None, "smart_money_bought": False, "smart_money_sold": True}) == "smart money exiting"
    )


def test_whale_signal_whale_sell_beats_smart_buy_per_priority():
    result = {"net_flow": -100.0, "smart_money_bought": True, "smart_money_sold": False}
    assert whale_signal(result) == "whales net selling"


def test_whale_signal_none_on_flat_flow():
    assert whale_signal({"net_flow": 0.0, "smart_money_bought": False, "smart_money_sold": False}) is None


def test_whale_signal_none_on_positive_flow():
    assert whale_signal({"net_flow": 250.0, "smart_money_bought": False, "smart_money_sold": False}) is None


def test_whale_signal_none_on_missing_data():
    assert (
        whale_signal({"net_flow": None, "whale_tx_count": None, "smart_money_bought": None, "smart_money_sold": None})
        is None
    )


def test_whale_signal_none_for_non_dict():
    assert whale_signal(None) is None
    assert whale_signal("not a dict") is None  # type: ignore[arg-type]


# ── _smart_money_direction (GMGN payload parser, pure) ─────────────────────


def test_smart_money_direction_bought():
    bought, sold, evidence = _smart_money_direction(
        {"smart_money_activity": [{"wallet": "0xaa", "buy_7d": 5, "sell_7d": 1}]}
    )
    assert bought is True
    assert sold is False
    assert "smart money net +4" in evidence


def test_smart_money_direction_sold():
    bought, sold, _ = _smart_money_direction({"smart_money_activity": [{"wallet": "0xaa", "buy_7d": 1, "sell_7d": 4}]})
    assert bought is False
    assert sold is True


def test_smart_money_direction_flat():
    bought, sold, evidence = _smart_money_direction(
        {"smart_money_activity": [{"wallet": "0xaa", "buy_7d": 2, "sell_7d": 2}]}
    )
    assert bought is False
    assert sold is False
    assert "flat" in evidence


def test_smart_money_direction_buy_pressure_fallback():
    bought, sold, _ = _smart_money_direction({"smart_money_activity": [], "buy_pressure": -30.0})
    assert bought is False
    assert sold is True


def test_smart_money_direction_top_level_buys_sells_fallback():
    bought, sold, _ = _smart_money_direction({"smart_money_activity": [], "buy_7d": 10, "sell_7d": 3})
    assert bought is True
    assert sold is False


def test_smart_money_direction_none_when_no_data():
    bought, sold, evidence = _smart_money_direction({"smart_money_activity": [], "source": "gmgn"})
    assert bought is None
    assert sold is None
    assert "no smart-money direction data" in evidence


# ── Helpers / default fetchers (missing lines) ─────────────────────────────


def test_to_float_unparseable_returns_none():
    assert _to_float("abc") is None
    assert _to_float(None) is None
    assert _to_float([]) is None  # type: ignore[arg-type]
    assert _to_float(3.5) == 3.5


def test_chain_candidates_exact_alias_and_empty():
    assert _chain_candidates("solana") == ["sol", "solana"]
    assert _chain_candidates("ethereum") == ["eth", "ethereum"]
    assert _chain_candidates("") == []
    assert _chain_candidates(None) == []  # type: ignore[arg-type]
    # Dedupes when the alias equals the chain.
    assert _chain_candidates("bsc") == ["bsc"]


def test_smart_money_direction_skips_non_dict_rows():
    bought, sold, _ = _smart_money_direction(
        {"smart_money_activity": ["junk", {"wallet": "0xaa", "buy_7d": 3, "sell_7d": 1}]}
    )
    assert bought is True
    assert sold is False


async def test_fetch_whale_flow_clickhouse_fail_open(monkeypatch):
    # Missing address/chain → neutral without touching ClickHouse.
    assert await fetch_whale_flow_clickhouse("", "solana") == {
        "net_flow": None,
        "whale_tx_count": None,
        "evidence": "",
    }

    # ClickHouse unavailable.
    monkeypatch.setattr(
        "app.domains.scanner.webhooks.providers.utils.get_clickhouse",
        lambda: None,
    )
    result = await fetch_whale_flow_clickhouse(TOKEN, CHAIN)
    assert result == {"net_flow": None, "whale_tx_count": None, "evidence": "clickhouse unavailable"}


async def test_fetch_whale_flow_clickhouse_queries_chain_candidates(monkeypatch):
    calls: list[str] = []

    class _FakeCH:
        def execute(self, query, params, **kwargs):
            calls.append(params["chain"])
            return [("a", "b", "123", "ts")]  # rows present → stop at first candidate

    monkeypatch.setattr(
        "app.domains.scanner.webhooks.providers.utils.get_clickhouse",
        lambda: _FakeCH(),
    )
    result = await fetch_whale_flow_clickhouse(TOKEN, "solana")
    assert result["whale_tx_count"] == 1
    assert calls[0] == "sol"  # alias tried first
    assert "1 whale transfer" in result["evidence"]


async def test_fetch_whale_flow_clickhouse_no_rows_evidence(monkeypatch):
    class _EmptyCH:
        def execute(self, query, params, **kwargs):
            return []

    monkeypatch.setattr(
        "app.domains.scanner.webhooks.providers.utils.get_clickhouse",
        lambda: _EmptyCH(),
    )
    result = await fetch_whale_flow_clickhouse(TOKEN, "ethereum")
    assert result["whale_tx_count"] == 0
    assert "no whale transfers in window" in result["evidence"]


async def test_fetch_whale_flow_clickhouse_fail_open_on_error(monkeypatch):
    class _BoomCH:
        def execute(self, query, params, **kwargs):
            raise RuntimeError("ch down")

    monkeypatch.setattr(
        "app.domains.scanner.webhooks.providers.utils.get_clickhouse",
        lambda: _BoomCH(),
    )
    result = await fetch_whale_flow_clickhouse(TOKEN, "solana")
    assert result == {"net_flow": None, "whale_tx_count": None, "evidence": ""}


async def test_fetch_smart_money_gmgn_fail_open(monkeypatch):
    # Missing address/chain.
    assert await fetch_smart_money_gmgn("", CHAIN) == {"bought": None, "sold": None, "evidence": ""}

    # GMGN provider unavailable.
    class _Unavailable:
        async def get_smart_money(self, address, chain):
            return {"source": "none"}

    monkeypatch.setattr(
        "app.databus.gmgn_provider.get_gmgn_provider",
        lambda: _Unavailable(),
    )
    result = await fetch_smart_money_gmgn(TOKEN, CHAIN)
    assert result == {"bought": None, "sold": None, "evidence": "gmgn unavailable"}


async def test_fetch_smart_money_gmgn_fail_open_on_error(monkeypatch):
    class _Boom:
        async def get_smart_money(self, address, chain):
            raise RuntimeError("gmgn down")

    monkeypatch.setattr(
        "app.databus.gmgn_provider.get_gmgn_provider",
        lambda: _Boom(),
    )
    result = await fetch_smart_money_gmgn(TOKEN, CHAIN)
    assert result == {"bought": None, "sold": None, "evidence": ""}


async def test_fetch_smart_money_gmgn_parses_direction(monkeypatch):
    class _FakeProvider:
        async def get_smart_money(self, address, chain):
            return {"smart_money_activity": [{"wallet": "0xaa", "buy_7d": 5, "sell_7d": 1}]}

    monkeypatch.setattr(
        "app.databus.gmgn_provider.get_gmgn_provider",
        lambda: _FakeProvider(),
    )
    result = await fetch_smart_money_gmgn(TOKEN, CHAIN)
    assert result["bought"] is True
    assert result["sold"] is False


# ── _compose / whale_flow outer failure ───────────────────────────────────


def test_compose_tolerates_non_dict_inputs():
    result = _compose(None, None)
    assert result == {
        "net_flow": None,
        "whale_tx_count": None,
        "smart_money_bought": None,
        "smart_money_sold": None,
        "evidence": None,
    }

    result = _compose(
        {"net_flow": -1.0, "whale_tx_count": "3", "evidence": "e1"},
        {"bought": "yes", "sold": False, "evidence": "e2"},
    )
    # String whale_tx_count is NOT coerced; non-bool bought is dropped.
    assert result["whale_tx_count"] is None
    assert result["smart_money_bought"] is None
    assert result["evidence"] == "e1 | e2"


@pytest.mark.asyncio
async def test_whale_flow_fail_open_when_fetcher_is_sync_broken():
    """A sync (non-awaitable) fetcher raises inside gather → caught fail-open."""

    def _sync_broken(address: str, chain: str) -> dict[str, Any]:
        raise TypeError("not async")

    result = await whale_flow(
        TOKEN,
        CHAIN,
        whale_fetcher=_sync_broken,  # type: ignore[arg-type]
        smart_money_fetcher=_empty_smart(),
    )
    assert result["net_flow"] is None
    assert result["smart_money_bought"] is None
    assert whale_signal(result) is None
