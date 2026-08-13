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

from app.domains.scanner.whale_flow_enrich import _smart_money_direction, whale_flow, whale_signal

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
