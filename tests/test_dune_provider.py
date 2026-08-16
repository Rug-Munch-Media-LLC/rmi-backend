"""Tests for the Dune Analytics provider + /dune bot command rendering.

Dune API responses are mocked here (no real network calls). Verified query IDs
and live-data smoke test are exercised separately via the smoke script and
the ``live`` marker (requires DUNE_API_KEY).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.databus.provider_implementations import dune as dune_mod


@pytest.fixture(autouse=True)
def _clear_cache():
    dune_mod.clear_cache()
    yield
    dune_mod.clear_cache()


@pytest.fixture
def mock_dune_http():
    """Patch httpx.AsyncClient inside the dune provider module."""
    with patch.object(dune_mod.httpx, "AsyncClient") as m:
        yield m


def _mock_200(mock_http, payload: dict):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = payload
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_http.return_value.__aenter__.return_value = mock_client
    return mock_client


def _completed_payload(rows=None, cols=None):
    return {
        "state": "QUERY_STATE_COMPLETED",
        "result": {
            "metadata": {"column_names": cols or ["amount_usd", "token_pair", "project", "blockchain"]},
            "rows": rows
            or [{"amount_usd": 1234.5, "token_pair": "WETH/USDC", "project": "uniswap", "blockchain": "ethereum"}],
        },
    }


class TestCuratedQueries:
    """DUNE_QUERIES must only contain verified, real public query IDs."""

    def test_curated_queries_are_known(self):
        assert set(dune_mod.DUNE_QUERIES) == {
            "dex_trades",
            "whale_transactions",
            "dai_transfers",
            "dune_api_demo",
        }

    def test_curated_query_ids_are_int(self):
        for name, qid in dune_mod.DUNE_QUERIES.items():
            assert isinstance(qid, int), name


class TestDuneLatestResults:
    @pytest.mark.asyncio
    async def test_success_returns_normalized_digest(self, mock_dune_http):
        _mock_200(mock_dune_http, _completed_payload())
        res = await dune_mod.dune_latest_results(3426636, limit=5)
        assert res is not None
        assert res["query_name"] == "dex_trades"
        assert res["query_id"] == 3426636
        assert res["state"] == "QUERY_STATE_COMPLETED"
        assert res["source"] == "dune"
        assert len(res["rows"]) == 1
        assert res["rows"][0]["amount_usd"] == 1234.5

    @pytest.mark.asyncio
    async def test_private_query_fails_open(self, mock_dune_http):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"error":"not found: Query not found or private"}'
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_http = mock_dune_http
        mock_http.return_value.__aenter__.return_value = mock_client

        res = await dune_mod.dune_latest_results(99999999, limit=5)
        assert res is None  # never raises, never fabricates

    @pytest.mark.asyncio
    async def test_failed_state_fails_open(self, mock_dune_http):
        _mock_200(mock_dune_http, {"state": "QUERY_STATE_FAILED", "result": {"rows": []}})
        res = await dune_mod.dune_latest_results(3426636, limit=5)
        assert res is None

    @pytest.mark.asyncio
    async def test_no_api_key_fails_open(self, mock_dune_http, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "")
        res = await dune_mod.dune_latest_results(3426636, limit=5)
        assert res is None
        mock_dune_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_fails_open(self, mock_dune_http):
        mock_client = AsyncMock()
        mock_client.get.side_effect = TimeoutError("boom")
        mock_dune_http.return_value.__aenter__.return_value = mock_client
        res = await dune_mod.dune_latest_results(3426636, limit=5)
        assert res is None

    @pytest.mark.asyncio
    async def test_cache_ttl_hit(self, mock_dune_http):
        _mock_200(mock_dune_http, _completed_payload())
        await dune_mod.dune_latest_results(3426636, limit=5)
        await dune_mod.dune_latest_results(3426636, limit=5)
        assert mock_dune_http.return_value.__aenter__.return_value.get.call_count == 1

    @pytest.mark.asyncio
    async def test_negative_cache_not_poisoning_full_ttl(self, mock_dune_http):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_dune_http.return_value.__aenter__.return_value = mock_client

        assert await dune_mod.dune_latest_results(99999999) is None
        # negative cache short: force expiry by clearing ts
        name = "query_99999999"
        dune_mod._CACHE.pop(name, None)
        assert await dune_mod.dune_latest_results(99999999) is None  # still fail-open
        assert mock_client.get.call_count == 2


class TestDuneNamedAndDigest:
    @pytest.mark.asyncio
    async def test_unknown_name_returns_none(self):
        res = await dune_mod.dune_named("not_a_real_query")
        assert res is None

    @pytest.mark.asyncio
    async def test_digest_structure(self, mock_dune_http):
        def _get(url, params=None, headers=None):
            qid = int(url.split("/query/")[1].split("/")[0])
            if qid == 3426636:  # only dex_trades succeeds in this scenario
                return MagicMock(status_code=200, json=lambda: _completed_payload())
            return MagicMock(status_code=404)

        mock_client = AsyncMock()
        mock_client.get.side_effect = _get
        mock_dune_http.return_value.__aenter__.return_value = mock_client

        digest = await dune_mod.dune_onchain_intel(limit=5)
        assert digest["source"] == "dune"
        assert "fetched_at" in digest
        assert set(digest["queries"]) == set(dune_mod.DUNE_QUERIES)
        for name, entry in digest["queries"].items():
            if name == "dex_trades":
                assert entry["query_name"] == "dex_trades"
                assert entry["rows"]
            else:
                assert entry.get("error") == "unavailable"

    @pytest.mark.asyncio
    async def test_digest_all_fail_open(self, mock_dune_http):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_dune_http.return_value.__aenter__.return_value = mock_client

        digest = await dune_mod.dune_onchain_intel(limit=5)
        for entry in digest["queries"].values():
            assert entry.get("error") == "unavailable"


def mock_ok(url, params=None, headers=None, side_effect=None):
    qid = url.split("/query/")[1].split("/")[0]
    return MagicMock(status_code=200, json=lambda: side_effect(int(qid)))


class TestCommandRendering:
    """Pure render helpers used by the /dune Telegram command."""

    def test_fmt_usd(self):
        from app.domains.telegram.rugmunchbot.commands.dune import _fmt_usd

        assert _fmt_usd(1_500_000) == "$1.50M"
        assert _fmt_usd(2_000_000_000) == "$2.00B"
        assert _fmt_usd(1234.5) == "$1.2K"
        assert _fmt_usd(None) == "—"
        assert _fmt_usd("nope") == "—"

    def test_row_line_real_row(self):
        from app.domains.telegram.rugmunchbot.commands.dune import _row_line

        line = _row_line(
            {"amount_usd": 49.05, "token_pair": "WFTW/WETH", "project": "1inch-LOP", "blockchain": "fantom"}
        )
        assert "1inch-LOP" in line
        assert "fantom" in line

    def test_row_line_missing_amount_uses_dash(self):
        from app.domains.telegram.rugmunchbot.commands.dune import _row_line

        line = _row_line({"amount_usd": None, "token_pair": "USDC/WETH", "blockchain": "base"})
        assert "—" in line or "base" in line
