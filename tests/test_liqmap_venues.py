"""Tests for the market-intel liquidation venue clients.

The Bybit row mapper is the regression guard: the v5 ticker names the
contract count ``openInterest`` and the mapper used to read
``open_interest`` - a key the payload never carries - so every Bybit row
reported OI 0 and the venue looked empty even when it answered.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from app.routers.market_intel.routes.liqmap_proxy import (
    fetch_geo_venue,
    pool_candidates,
)
from app.routers.market_intel.routes.liqmap_rows import (
    _binance_row,
    _bybit_row,
    collect_rows,
)
from app.routers.market_intel.routes.liqmap_venues import (
    _binance_liquidation_snapshot,
    _bybit_liquidation_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

BYBIT_URL = "https://api.bybit.com/v5/market/tickers"
BINANCE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
BYBIT_TICKER = {
    "symbol": "BTCUSDT",
    "openInterest": 100.0,
    "markPrice": 50.0,
    "fundingRate": 0.0001,
}


@pytest.fixture
def direct_pool() -> Iterator[None]:
    """Run the test with an empty proxy pool, so requests leave directly."""
    previous = os.environ.get("WEBSHARE_PROXY_LIST")
    os.environ["WEBSHARE_PROXY_LIST"] = ""
    yield
    if previous is None:
        os.environ.pop("WEBSHARE_PROXY_LIST", None)
    else:
        os.environ["WEBSHARE_PROXY_LIST"] = previous


def test_bybit_row_reads_open_interest_contracts() -> None:
    parsed = _bybit_row(BYBIT_TICKER)
    assert parsed is not None
    symbol, values = parsed
    assert symbol == "BTC"
    assert values["oi_usd"] == 5000.0
    assert values["funding"] == 0.0001
    assert values["mark"] == 50.0


def test_bybit_row_falls_back_to_open_interest_value() -> None:
    parsed = _bybit_row({"symbol": "ETHUSDT", "openInterestValue": 1234.5})
    assert parsed is not None
    assert parsed[1]["oi_usd"] == 1234.5


def test_bybit_row_without_symbol_is_skipped() -> None:
    assert _bybit_row({"openInterest": 1.0}) is None


def test_collect_rows_skips_unparseable_rows() -> None:
    parsed = collect_rows([BYBIT_TICKER, {"nope": 1}], _bybit_row)
    assert list(parsed) == ["BTC"]


def test_binance_row_reports_mark_and_funding_without_oi() -> None:
    parsed = _binance_row({"symbol": "BTCUSDT", "markPrice": "100.5", "lastFundingRate": "-0.0002"})
    assert parsed is not None
    assert parsed[1] == {"oi_usd": 0.0, "funding": -0.0002, "mark": 100.5}


def test_pool_candidates_is_direct_without_a_pool(direct_pool: None) -> None:
    assert pool_candidates(4) == [None]


@respx.mock
async def test_fetch_geo_venue_returns_the_first_answer(direct_pool: None) -> None:
    respx.get(BYBIT_URL).mock(return_value=httpx.Response(200, json={"result": {"list": []}}))
    response, proxy = await fetch_geo_venue(BYBIT_URL, {"category": "linear"})
    assert response is not None
    assert response.status_code == 200
    assert proxy is None


@respx.mock
async def test_fetch_geo_venue_gives_up_on_a_refusal(direct_pool: None) -> None:
    respx.get(BYBIT_URL).mock(return_value=httpx.Response(403, json={}))
    response, _proxy = await fetch_geo_venue(BYBIT_URL)
    assert response is None


@respx.mock
async def test_bybit_snapshot_carries_open_interest(direct_pool: None) -> None:
    respx.get(BYBIT_URL).mock(return_value=httpx.Response(200, json={"result": {"list": [BYBIT_TICKER]}}))
    rows = await _bybit_liquidation_snapshot()
    assert rows["BTC"]["oi_usd"] == 5000.0


@respx.mock
async def test_binance_snapshot_reads_the_list(direct_pool: None) -> None:
    respx.get(BINANCE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[{"symbol": "BTCUSDT", "markPrice": "100.5", "lastFundingRate": "-0.0002"}],
        )
    )
    rows = await _binance_liquidation_snapshot()
    assert rows["BTC"]["mark"] == 100.5
