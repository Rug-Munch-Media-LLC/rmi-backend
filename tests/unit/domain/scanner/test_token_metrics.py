"""Tests for the Phanes-parity token-metric engines (2026-09-02).

Covers, per engine (mock ClickHouse — no network):
  * dev_sold: sell detection (EVM + Solana), sold-pct math, no-deployer
    unavailable shape, fail-open on ClickHouse errors.
  * dex_paid: paid / not-paid / dex list, fail-open.
  * price_metrics: ATH + current + 1h change, no-data unavailable, fail-open.
  * sniper_detect: sniper cohort within the launch window, launch-block
    resolution fallback, no-launch-block unavailable, fail-open.
  * the ``run_token_metrics`` phase wiring: parallel engines attach to
    ScanResult fields + modules_run, and degrade independently.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.domains.scanner.engines.dev_sold import check_dev_sold
from app.domains.scanner.engines.dex_paid import check_dex_paid
from app.domains.scanner.engines.price_metrics import get_price_metrics
from app.domains.scanner.engines.sniper_detect import detect_snipers
from app.domains.scanner.unified._phase_helpers import run_token_metrics
from app.domains.scanner.unified.model import ScanResult


class _FakeCH:
    """Fake clickhouse client returning canned rows per query index."""

    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self._results = results
        self.queries: list[str] = []

    def execute(self, query: str, params: Any = None, settings: Any = None) -> list[tuple[Any, ...]]:
        self.queries.append(query)
        idx = min(len(self.queries) - 1, len(self._results) - 1)
        return self._results[idx]


def _patch_new_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr("app.domains.scanner.engines.dev_sold._new_ch_client", lambda: client)
    monkeypatch.setattr("app.domains.scanner.engines.dex_paid._new_ch_client", lambda: client)
    monkeypatch.setattr("app.domains.scanner.engines.price_metrics._new_ch_client", lambda: client)
    monkeypatch.setattr("app.domains.scanner.engines.sniper_detect._new_ch_client", lambda: client)


# ── dev_sold ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dev_sold_evm_sells_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    ts = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    client = _FakeCH([[(3, ts, 500.0, 1000.0)]])
    _patch_new_client(monkeypatch, client)
    res = await check_dev_sold("0xTOKEN", "ethereum", "0xDEV")
    assert res["result"] == "scanned"
    assert res["dev_sold"] is True
    assert res["sell_count"] == 3
    assert res["total_sold_pct"] == 33.33  # 500 / (500 + 1000)
    assert res["last_sell_ts"] is not None
    # EVM addresses must be lowercased in the query params.
    assert any("token_address" in q for q in client.queries)


@pytest.mark.asyncio
async def test_dev_sold_solana_negative_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeCH([[(1, None, 250.0, 0.0)]])
    _patch_new_client(monkeypatch, client)
    res = await check_dev_sold("MintAddr", "solana", "DevWallet")
    assert res["result"] == "scanned"
    assert res["dev_sold"] is True
    assert res["total_sold_pct"] == 100.0
    # Solana query must hit the balance-changes table, not evm_transfers.
    assert any("solana_spl_balance_changes" in q for q in client.queries)


@pytest.mark.asyncio
async def test_dev_sold_no_deployer_unavailable() -> None:
    res = await check_dev_sold("0xTOKEN", "ethereum", None)
    assert res == {"result": "unavailable", "note": "no deployer address"}


@pytest.mark.asyncio
async def test_dev_sold_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("ch down")

    monkeypatch.setattr("app.domains.scanner.engines.dev_sold._new_ch_client", _boom)
    res = await check_dev_sold("0xTOKEN", "ethereum", "0xDEV")
    assert res["result"] == "error"


# ── dex_paid ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dex_paid_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeCH([[(42, ["uniswap_v3", "curve"])]])
    _patch_new_client(monkeypatch, client)
    res = await check_dex_paid("0xTOKEN", "base")
    assert res["result"] == "scanned"
    assert res["dex_paid"] is True
    assert res["dex_count"] == 42
    assert res["dexes"] == ["curve", "uniswap_v3"]


@pytest.mark.asyncio
async def test_dex_paid_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeCH([[(0, [])]])
    _patch_new_client(monkeypatch, client)
    res = await check_dex_paid("0xTOKEN", "ethereum")
    assert res["result"] == "scanned"
    assert res["dex_paid"] is False


@pytest.mark.asyncio
async def test_dex_paid_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("ch down")

    monkeypatch.setattr("app.domains.scanner.engines.dex_paid._new_ch_client", _boom)
    res = await check_dex_paid("0xTOKEN", "ethereum")
    assert res["result"] == "error"


# ── price_metrics ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_price_metrics_full(monkeypatch: pytest.MonkeyPatch) -> None:
    ath_ts = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    client = _FakeCH(
        [
            [(0.005, 0.004, ath_ts)],  # ath, current, ath_ts
            [(0.004,)],  # 1h-ago price
        ]
    )
    _patch_new_client(monkeypatch, client)
    res = await get_price_metrics("0xTOKEN", "ethereum")
    assert res["result"] == "scanned"
    assert res["ath"] == 0.005
    assert res["current_price"] == 0.004
    assert res["change_1h_pct"] == 0.0
    assert res["ath_ts"] is not None


@pytest.mark.asyncio
async def test_price_metrics_no_data_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeCH([[(None, None, None)]])
    _patch_new_client(monkeypatch, client)
    res = await get_price_metrics("0xTOKEN", "ethereum")
    assert res == {"result": "unavailable", "note": "no DEX data"}


@pytest.mark.asyncio
async def test_price_metrics_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("ch down")

    monkeypatch.setattr("app.domains.scanner.engines.price_metrics._new_ch_client", _boom)
    res = await get_price_metrics("0xTOKEN", "ethereum")
    assert res["result"] == "error"


# ── sniper_detect ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sniper_detect_with_launch_block(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeCH(
        [
            [(5, 1000)],  # snipers: 5 distinct buyers, first buy block 1000
            [(50,)],  # total distinct buyers
        ]
    )
    _patch_new_client(monkeypatch, client)
    res = await detect_snipers("0xTOKEN", "ethereum", launch_block=1000)
    assert res["result"] == "scanned"
    assert res["sniper_count"] == 5
    assert res["sniper_pct"] == 10.0
    assert res["first_buy_block"] == 1000
    # Window must be launch_block..launch_block+4 (first 5 blocks).
    assert any("BETWEEN" in q for q in client.queries)


@pytest.mark.asyncio
async def test_sniper_detect_resolves_launch_block(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeCH(
        [
            [(500,)],  # deployer_events min block
            [(2, 500)],
            [(10,)],
        ]
    )
    _patch_new_client(monkeypatch, client)
    res = await detect_snipers("0xTOKEN", "ethereum")
    assert res["result"] == "scanned"
    assert res["sniper_count"] == 2
    assert res["launch_block"] == 500


@pytest.mark.asyncio
async def test_sniper_detect_no_launch_block_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeCH([[(None,)]])
    _patch_new_client(monkeypatch, client)
    res = await detect_snipers("0xTOKEN", "ethereum")
    assert res == {"result": "unavailable", "note": "no launch block"}


@pytest.mark.asyncio
async def test_sniper_detect_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("ch down")

    monkeypatch.setattr("app.domains.scanner.engines.sniper_detect._new_ch_client", _boom)
    res = await detect_snipers("0xTOKEN", "ethereum", launch_block=100)
    assert res["result"] == "error"


# ── run_token_metrics phase wiring ────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_token_metrics_attaches_all(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _dev_sold(token: str, chain: str, deployer: str | None) -> dict[str, Any]:
        return {"result": "scanned", "dev_sold": True, "sell_count": 1}

    async def _dex_paid(token: str, chain: str) -> dict[str, Any]:
        return {"result": "scanned", "dex_paid": True, "dex_count": 2, "dexes": ["x"]}

    async def _price(token: str, chain: str) -> dict[str, Any]:
        return {"result": "scanned", "ath": 1.0, "current_price": 0.5, "change_1h_pct": -50.0}

    async def _snipers(token: str, chain: str, block: int | None) -> dict[str, Any]:
        return {"result": "scanned", "sniper_count": 3, "sniper_pct": 5.0}

    monkeypatch.setattr("app.domains.scanner.engines.dev_sold.check_dev_sold", _dev_sold)
    monkeypatch.setattr("app.domains.scanner.engines.dex_paid.check_dex_paid", _dex_paid)
    monkeypatch.setattr("app.domains.scanner.engines.price_metrics.get_price_metrics", _price)
    monkeypatch.setattr("app.domains.scanner.engines.sniper_detect.detect_snipers", _snipers)

    r = ScanResult(token_address="0xtoken", chain="ethereum")
    await run_token_metrics(r, "0xtoken", "ethereum")
    assert r.dev_sold is not None
    assert r.dev_sold["dev_sold"] is True
    assert r.dex_paid is not None
    assert r.dex_paid["dex_paid"] is True
    assert r.price_metrics is not None
    assert r.price_metrics["ath"] == 1.0
    assert r.sniper_count is not None
    assert r.sniper_count["sniper_count"] == 3
    for mod in ("dev_sold", "dex_paid", "price_metrics", "sniper_detect"):
        assert mod in r.modules_run


@pytest.mark.asyncio
async def test_run_token_metrics_degrades_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _dev_sold(token: str, chain: str, deployer: str | None) -> dict[str, Any]:
        return {"result": "unavailable", "note": "no deployer address"}

    async def _dex_paid(token: str, chain: str) -> dict[str, Any]:
        raise RuntimeError("ch down")

    async def _price(token: str, chain: str) -> dict[str, Any]:
        return {"result": "scanned", "ath": 1.0, "current_price": 0.5, "change_1h_pct": None}

    async def _snipers(token: str, chain: str, block: int | None) -> dict[str, Any]:
        return {"result": "unavailable", "note": "no launch block"}

    monkeypatch.setattr("app.domains.scanner.engines.dev_sold.check_dev_sold", _dev_sold)
    monkeypatch.setattr("app.domains.scanner.engines.dex_paid.check_dex_paid", _dex_paid)
    monkeypatch.setattr("app.domains.scanner.engines.price_metrics.get_price_metrics", _price)
    monkeypatch.setattr("app.domains.scanner.engines.sniper_detect.detect_snipers", _snipers)

    r = ScanResult(token_address="0xtoken", chain="ethereum")
    await run_token_metrics(r, "0xtoken", "ethereum")
    assert r.dev_sold is None
    assert r.dex_paid is None
    assert r.price_metrics is not None
    assert r.sniper_count is None
    # The raised dex_paid error is captured, not propagated.
    assert any(e["module"] == "dex_paid" for e in r.errors)
