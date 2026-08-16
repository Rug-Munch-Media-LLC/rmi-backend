"""Unit tests for ``own_cross_token_link`` — recurring-cluster + serial-launcher.

Covers the pure line builders (recurring cluster, deployer link, funder link),
the fail-open ``unavailable`` payload (ClickHouse absent), and the scanned
payload built from fake query rows. No real ClickHouse / knowledge graph — the
query layer and KG fallback are patched.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.scanner.engines import own_cross_token_link as xt


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    xt.clear_cache()
    yield
    xt.clear_cache()


# ── Pure line builders ──


class TestBuildRecurringLine:
    def test_recurring_cluster_line_format(self) -> None:
        line = xt._build_recurring_line(7, 3)
        assert line == ("\U0001f578\ufe0f Recurring cluster: 7 wallets also bought 3 prior launches")

    def test_single_prior_launch(self) -> None:
        line = xt._build_recurring_line(2, 1)
        assert line.endswith("2 wallets also bought 1 prior launch")

    def test_fail_open_dash_when_no_recurrence(self) -> None:
        assert xt._build_recurring_line(0, 3) == "\u2014"
        assert xt._build_recurring_line(7, 0) == "\u2014"
        assert xt._build_recurring_line(0, 0) == "\u2014"


class TestBuildDeployerLinkLine:
    def test_link_line_with_token_ids(self) -> None:
        line = xt._build_deployer_link_line(4, ["0xabc\u2026", "0xdef\u2026"])
        assert line == "\U0001f517 Deployer linked to 4 prior launches: 0xabc\u2026, 0xdef\u2026"

    def test_more_than_three_ids_gets_more_suffix(self) -> None:
        ids = ["0xaaa\u2026", "0xbbb\u2026", "0xccc\u2026", "0xddd\u2026"]
        line = xt._build_deployer_link_line(4, ids)
        assert line.startswith("\U0001f517 Deployer linked to 4 prior launches: 0xaaa\u2026, 0xbbb\u2026, 0xccc\u2026")
        assert line.endswith("+1 more")

    def test_fail_open_dash_without_prior_tokens(self) -> None:
        assert xt._build_deployer_link_line(0, []) == "\u2014"
        assert xt._build_deployer_link_line(2, []) == "\u2014"


class TestBuildFunderLinkLine:
    def test_plural(self) -> None:
        assert xt._build_funder_link_line(2) == "\U0001f517 Funder also funded 2 other deployers"

    def test_singular(self) -> None:
        assert xt._build_funder_link_line(1) == "\U0001f517 Funder also funded 1 other deployer"

    def test_fail_open_dash(self) -> None:
        assert xt._build_funder_link_line(0) == "\u2014"


class TestShortId:
    def test_truncates_long_address(self) -> None:
        assert xt._short_id("0xabcdef123456") == "0xabcd\u2026"

    def test_short_address_passthrough(self) -> None:
        assert xt._short_id("0xabc") == "0xabc"

    def test_empty_returns_dash(self) -> None:
        assert xt._short_id("") == "\u2014"


# ── Public API: fail-open without ClickHouse ──


class TestGetOwnCrossTokenLink:
    async def test_unavailable_payload_when_query_layer_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            return None

        monkeypatch.setattr(xt, "run_sync", _fake_run_sync)
        result = await xt.get_own_cross_token_link("0xtoken", "ethereum", deployer="0xdev")
        assert result["result"] == "unavailable"
        assert result["recurring_cluster_line"] == "\u2014"
        assert result["deployer_link_line"] == "\u2014"
        assert result["funder_link_line"] == "\u2014"

    async def test_never_raises_on_query_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("clickhouse down")

        monkeypatch.setattr(xt, "run_sync", _boom)
        result = await xt.get_own_cross_token_link("0xtoken", "ethereum")
        assert result["result"] == "unavailable"
        assert result["recurring_cluster_line"] == "\u2014"

    async def test_scanned_recurring_cluster_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if fn is xt._query_launch_cluster:
                return {
                    "deploy_block": 1000,
                    "launch_buyers": [
                        {"buyer": "0xw1", "block_number": 1001, "buy_count": 1, "amount": 10.0},
                        {"buyer": "0xw2", "block_number": 1001, "buy_count": 1, "amount": 10.0},
                        {"buyer": "0xw3", "block_number": 1002, "buy_count": 1, "amount": 10.0},
                    ],
                    "cluster_blocks": [],
                }
            if fn is xt._query_first_funders:
                return [
                    ("0xw1", "0xfunder1", "0xtx", None),
                    ("0xw2", "0xfunder1", "0xtx", None),
                    ("0xw3", "0xfunder2", "0xtx", None),
                ]
            if fn is xt._query_recurring_prior_tokens:
                return [
                    ("0xprior1", 3, ["0xw1", "0xw2", "0xfunder1"]),
                    ("0xprior2", 2, ["0xw1", "0xw3"]),
                    ("0xprior3", 2, ["0xfunder1", "0xfunder2"]),
                ]
            return None

        monkeypatch.setattr(xt, "run_sync", _fake_run_sync)
        result = await xt.get_own_cross_token_link("0xtoken", "ethereum")
        assert result["result"] == "scanned"
        assert result["prior_launch_count"] == 3
        assert result["recurring_wallets"] == 5  # w1..w3 + funder1 + funder2 (union across prior tokens)
        assert result["recurring_cluster_line"].startswith("\U0001f578\ufe0f Recurring cluster:")
        assert result["prior_launch_tokens"][0]["token"] == "0xprior1"

    async def test_serial_launcher_deployer_link_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if fn is xt._query_launch_cluster:
                return {"deploy_block": 1000, "launch_buyers": [], "cluster_blocks": []}
            if fn is xt._query_per_token_deployments:
                return [
                    {"token_address": "0xprior_a", "chain": "ethereum", "deployed_ts": None, "deploy_tx": None},
                    {"token_address": "0xprior_b", "chain": "ethereum", "deployed_ts": None, "deploy_tx": None},
                    {"token_address": "0xcurrent", "chain": "ethereum", "deployed_ts": None, "deploy_tx": None},
                ]
            if fn is xt._query_funder:
                return {
                    "from_address": "0xfunder",
                    "first_ts": None,
                    "first_tx": None,
                    "first_token": None,
                    "is_native": True,
                }
            if fn is xt._query_same_funder_deployers:
                return 3
            return None

        monkeypatch.setattr(xt, "run_sync", _fake_run_sync)
        result = await xt.get_own_cross_token_link("0xcurrent", "ethereum", deployer="0xdev")
        assert result["result"] == "scanned"
        # current token excluded from the deployer's prior list.
        assert result["deployer_prior_count"] == 2
        assert result["deployer_prior_tokens"] == ["0xprio\u2026", "0xprio\u2026"]
        assert result["deployer_link_line"].startswith("\U0001f517 Deployer linked to 2 prior launches:")
        # funder armed 3 deployers total → 2 other deployers.
        assert result["funder_deployer_count"] == 2
        assert result["funder_link_line"] == "\U0001f517 Funder also funded 2 other deployers"

    async def test_fail_open_when_deployer_has_no_prior_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if fn is xt._query_launch_cluster:
                return {"deploy_block": 1000, "launch_buyers": [], "cluster_blocks": []}
            if fn is xt._query_per_token_deployments:
                return []  # no prior tokens
            if fn is xt._query_funder:
                return {
                    "from_address": "0xfunder",
                    "first_ts": None,
                    "first_tx": None,
                    "first_token": None,
                    "is_native": True,
                }
            if fn is xt._query_same_funder_deployers:
                return 1  # only this deployer — not a serial launcher
            return None

        monkeypatch.setattr(xt, "run_sync", _fake_run_sync)
        result = await xt.get_own_cross_token_link("0xtoken", "ethereum", deployer="0xdev")
        assert result["result"] == "unavailable"
        assert result["deployer_link_line"] == "\u2014"
        assert result["funder_link_line"] == "\u2014"

    async def test_kg_fallback_used_when_clickhouse_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if fn is xt._query_launch_cluster:
                return {
                    "deploy_block": 1000,
                    "launch_buyers": [
                        {"buyer": "0xw1", "block_number": 1001, "buy_count": 1, "amount": 10.0},
                        {"buyer": "0xw2", "block_number": 1001, "buy_count": 1, "amount": 10.0},
                    ],
                    "cluster_blocks": [],
                }
            if fn is xt._query_first_funders:
                return [("0xw1", "0xfunder1", "0xtx", None), ("0xw2", "0xfunder1", "0xtx", None)]
            # ClickHouse prior-token query returns nothing → KG fallback.
            return None

        async def _fake_kg(wallets: list[str]) -> list[tuple[str, int, list[str]]]:
            return [("0xkg_prior", 2, ["0xw1", "0xw2"])]

        monkeypatch.setattr(xt, "run_sync", _fake_run_sync)
        monkeypatch.setattr(xt, "_kg_prior_token_overlap", _fake_kg)
        result = await xt.get_own_cross_token_link("0xtoken", "ethereum")
        assert result["result"] == "scanned"
        assert result["prior_launch_count"] == 1
        assert result["recurring_cluster_line"].endswith("2 wallets also bought 1 prior launch")

    async def test_cache_hit_returns_clean_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            return None

        monkeypatch.setattr(xt, "run_sync", _fake_run_sync)
        await xt.get_own_cross_token_link("0xtoken", "ethereum", deployer="0xdev")
        result = await xt.get_own_cross_token_link("0xtoken", "ethereum", deployer="0xdev")
        assert result["result"] == "unavailable"
        assert "_cached_at" not in result
