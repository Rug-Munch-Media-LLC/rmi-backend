"""Unit tests for the degodded funding_analysis split modules.

Covers ``_funding_results.py`` (pure graceful-degradation result builders)
and ``_funding_queries.py`` (ClickHouse query layer with a mocked client —
no network/DB).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.domains.scanner.engines import _funding_queries as fq, _funding_results as fr

# ── result builders (pure) ─────────────────────────────────────────────────


def test_unavailable_result_shape() -> None:
    r = fr._unavailable_result("0xabc", "ethereum")
    assert r["check"] == "funding_sources"
    assert r["result"] == "unavailable"
    assert r["source"] == "own"
    assert r["token_address"] == "0xabc"
    assert r["chain"] == "ethereum"
    assert r["holders_analyzed"] == 0
    assert all(r[k] == 0.0 for k in ("exchange_funded_pct", "fresh_wallet_pct", "mixer_funded_pct", "unknown_pct"))
    assert r["top_funders"] == []
    assert r["holder_details"] == []
    assert r["risk_assessment"] == "LOW"
    assert r["note"] == "clickhouse unavailable"


def test_unavailable_result_custom_reason() -> None:
    r = fr._unavailable_result("0xabc", "solana", reason="timeout")
    assert r["note"] == "timeout"


def test_error_result_shape() -> None:
    r = fr._error_result("0xabc", "ethereum", "invalid token")
    assert r["check"] == "funding_sources"
    assert r["result"] == "error"
    assert r["error"] == "invalid token"
    assert "note" not in r


def test_freshness_unavailable_shape() -> None:
    r = fr._freshness_unavailable("0xabc", "ethereum")
    assert r["check"] == "wallet_freshness"
    assert r["result"] == "unavailable"
    assert r["address"] == "0xabc"
    assert r["age_days"] == 0.0
    assert r["total_txs"] == 0
    assert r["funded_by_cex"] is False
    assert r["first_funder"] is None
    assert r["classification"] == "ESTABLISHED"


def test_overlap_unavailable_and_error() -> None:
    u = fr._overlap_unavailable("0xa", "0xb", "ethereum")
    assert u["check"] == "holder_overlap"
    assert u["result"] == "unavailable"
    assert u["token_a"] == "0xa"
    assert u["token_b"] == "0xb"
    assert u["overlap_pct"] == 0.0
    assert u["token_b_is_scam"] is False
    e = fr._overlap_error("0xa", "0xb", "ethereum", "same token")
    assert e["result"] == "error"
    assert e["error"] == "same token"


def test_scam_overlap_error_shape() -> None:
    r = fr._scam_overlap_error("0xabc", "ethereum", "no scam list")
    assert r["check"] == "scam_overlap"
    assert r["result"] == "error"
    assert r["scam_tokens_checked"] == 0
    assert r["max_overlap_pct"] == 0.0
    assert r["worst_token"] is None
    assert r["comparisons"] == []


def test_wallet_metrics_unavailable_and_error() -> None:
    u = fr._wallet_metrics_unavailable("0xabc", "ethereum")
    assert u["check"] == "wallet_metrics"
    assert u["result"] == "unavailable"
    assert u["fresh_wallet_count"] == 0
    assert u["cex_funded_count"] == 0
    assert u["first_tx_count"] == 0
    e = fr._wallet_metrics_error("0xabc", "ethereum", "bad address")
    assert e["result"] == "error"
    assert e["error"] == "bad address"
    assert "note" not in e


# ── first-tx row merge (pure) ──────────────────────────────────────────────


def test_merge_first_tx_rows_keeps_earliest_per_holder_case_insensitive() -> None:
    in_rows = [("0xAAA", 100, "tokA", "tx1"), ("0xbbb", 200, "tokB", "tx2")]
    out_rows = [("0xaaa", 50, "tokC", "tx3")]
    merged = fq._merge_first_tx_rows(in_rows, out_rows)
    assert ("0xaaa", 50, "tokC", "tx3") in merged
    assert ("0xbbb", 200, "tokB", "tx2") in merged
    assert len(merged) == 2


def test_merge_first_tx_rows_handles_none_and_empty() -> None:
    assert fq._merge_first_tx_rows(None, None) == []
    assert fq._merge_first_tx_rows([], []) == []
    assert fq._merge_first_tx_rows([(None, 1, "x", "y")], []) == []


# ── query layer with mocked ClickHouse client ──────────────────────────────


def _mock_client(rows: list[tuple] | None = None) -> MagicMock:
    client = MagicMock()
    client.execute.return_value = rows or []
    return client


def test_query_holder_first_tx_in_uses_dedicated_client_and_params() -> None:
    client = _mock_client([("0xaaa", 100, "tok", "tx1")])
    rows = fq._query_holder_first_tx_in(["0xaaa", "0xbbb"], "ethereum", client=client)
    assert rows == [("0xaaa", 100, "tok", "tx1")]
    args, kwargs = client.execute.call_args
    assert args[1] == {"holders": ("0xaaa", "0xbbb"), "chain": "ethereum"}
    assert kwargs["settings"]["max_execution_time"] == fq._QUERY_TIMEOUT_SECONDS


def test_query_holder_first_tx_in_empty_holders_short_circuits() -> None:
    client = _mock_client()
    assert fq._query_holder_first_tx_in([], "ethereum", client=client) is None
    client.execute.assert_not_called()


def test_query_holder_first_tx_falls_back_to_shared_client() -> None:
    client = _mock_client([("0xaaa", 100, "tok", "tx1")])
    with patch.object(fq, "_get_ch_client", return_value=client):
        rows = fq._query_holder_first_tx_in(["0xaaa"], "ethereum")
    assert rows == [("0xaaa", 100, "tok", "tx1")]


def test_query_top_holders_solana_branch() -> None:
    client = _mock_client([("holder1", 5.0)])
    with patch.object(fq, "_get_ch_client", return_value=client):
        rows = fq._query_top_holders("So11111111111111111111111111111111111111112", "solana", 10)
    assert rows == [("holder1", 5.0)]
    sql = client.execute.call_args.args[0]
    assert "solana_spl_balance_changes" in sql


def test_query_top_holders_evm_holder_balances_then_legacy_fallback() -> None:
    client = MagicMock()
    client.execute.side_effect = [[("0x1", 100.0)], [("0x2", 50.0)]]
    with patch.object(fq, "_get_ch_client", return_value=client):
        rows = fq._query_top_holders("0xabc", "ethereum", 10)
    assert rows == [("0x1", 100.0)]
    first_sql = client.execute.call_args_list[0].args[0]
    assert "holder_balances" in first_sql


def test_query_top_holders_evm_empty_holder_balances_triggers_legacy() -> None:
    client = MagicMock()
    client.execute.side_effect = [[], [("0x2", 50.0)]]
    with patch.object(fq, "_get_ch_client", return_value=client):
        rows = fq._query_top_holders("0xabc", "ethereum", 10)
    assert rows == [("0x2", 50.0)]
    assert client.execute.call_count == 2
    second_sql = client.execute.call_args_list[1].args[0]
    assert "evm_transfers" in second_sql


def test_query_top_holders_client_none_and_exception() -> None:
    with patch.object(fq, "_get_ch_client", return_value=None):
        assert fq._query_top_holders("0xabc", "ethereum", 10) is None
    client = MagicMock()
    client.execute.side_effect = OSError("clickhouse down")
    with patch.object(fq, "_get_ch_client", return_value=client):
        assert fq._query_top_holders("0xabc", "ethereum", 10) is None


def test_query_first_funders_uses_tuple_holders() -> None:
    client = _mock_client([("0xaaa", "0xfunder", "0xtx", 100)])
    with patch.object(fq, "_get_ch_client", return_value=client):
        rows = fq._query_first_funders(["0xaaa"], "ethereum")
    assert rows == [("0xaaa", "0xfunder", "0xtx", 100)]
    assert client.execute.call_args.args[1]["holders"] == ("0xaaa",)


def test_query_funder_tx_counts() -> None:
    client = _mock_client([("0xaaa", 7)])
    with patch.object(fq, "_get_ch_client", return_value=client):
        rows = fq._query_funder_tx_counts(["0xaaa"], "ethereum")
    assert rows == [("0xaaa", 7)]
    assert client.execute.call_args.args[1]["funders"] == ("0xaaa",)


def test_query_wallet_stats_and_first_funder() -> None:
    client = _mock_client([(100, 5)])
    with patch.object(fq, "_get_ch_client", return_value=client):
        assert fq._query_wallet_stats("0xaaa", "ethereum") == [(100, 5)]
    client = _mock_client([("0xfunder",)])
    with patch.object(fq, "_get_ch_client", return_value=client):
        assert fq._query_wallet_first_funder("0xaaa", "ethereum") == [("0xfunder",)]


def test_query_token_coverage_start() -> None:
    client = MagicMock()
    client.execute.return_value = [(1700000000,)]
    with patch.object(fq, "_get_ch_client", return_value=client):
        assert fq._query_token_coverage_start("0xabc", "ethereum") == 1700000000
    client.execute.return_value = [(None,)]
    with patch.object(fq, "_get_ch_client", return_value=client):
        assert fq._query_token_coverage_start("0xabc", "ethereum") is None
    client.execute.return_value = []
    with patch.object(fq, "_get_ch_client", return_value=client):
        assert fq._query_token_coverage_start("0xabc", "ethereum") is None


def test_query_holder_set_solana_and_evm() -> None:
    client = _mock_client([("holder1",)])
    with patch.object(fq, "_get_ch_client", return_value=client):
        rows = fq._query_holder_set("So11111111111111111111111111111111111111112", "solana", 500)
    assert rows == [("holder1",)]
    assert "solana_spl_balance_changes" in client.execute.call_args.args[0]
    client = _mock_client([("0xaaa",)])
    with patch.object(fq, "_get_ch_client", return_value=client):
        rows = fq._query_holder_set("0xabc", "ethereum", 500)
    assert rows == [("0xaaa",)]
    assert "evm_transfers" in client.execute.call_args.args[0]


def test_query_known_scam_tokens_lowercases() -> None:
    client = _mock_client([("0xABC",), ("0xDEF",)])
    with patch.object(fq, "_get_ch_client", return_value=client):
        rows = fq._query_known_scam_tokens("ethereum", 50)
    assert rows == ["0xabc", "0xdef"]
    assert client.execute.call_args.args[1] == {"chain": "ethereum", "limit": 50}


def test_query_token_scam_status_checks_both_tables() -> None:
    client = MagicMock()
    client.execute.side_effect = [[("1",)], []]
    with patch.object(fq, "_get_ch_client", return_value=client):
        assert fq._query_token_scam_status("0xabc", "ethereum") is True
    assert client.execute.call_count == 1  # short-circuits after first hit

    client = MagicMock()
    client.execute.side_effect = [[], [("1",)]]
    with patch.object(fq, "_get_ch_client", return_value=client):
        assert fq._query_token_scam_status("0xabc", "ethereum") is True
    assert client.execute.call_count == 2
    assert "scam_addresses" in client.execute.call_args_list[1].args[0]

    client = MagicMock()
    client.execute.side_effect = [[], []]
    with patch.object(fq, "_get_ch_client", return_value=client):
        assert fq._query_token_scam_status("0xabc", "ethereum") is False


def test_query_graceful_failures_when_client_none() -> None:
    with patch.object(fq, "_get_ch_client", return_value=None):
        assert fq._query_first_funders(["0xaaa"], "ethereum") is None
        assert fq._query_funder_tx_counts(["0xaaa"], "ethereum") is None
        assert fq._query_wallet_stats("0xaaa", "ethereum") is None
        assert fq._query_wallet_first_funder("0xaaa", "ethereum") is None
        assert fq._query_token_coverage_start("0xabc", "ethereum") is None
        assert fq._query_holder_set("0xabc", "ethereum") is None
        assert fq._query_known_scam_tokens("ethereum", 10) == []
        assert fq._query_token_scam_status("0xabc", "ethereum") is False
