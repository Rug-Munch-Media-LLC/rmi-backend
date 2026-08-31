"""Unit tests for whale-feed enrichment (app/routers/v1/whale_enrichment.py).

Covers:
  - stable_decimals / stable_usd: raw → human conversion, meta override,
    fallback map, invalid input.
  - classify_labels: exchange / flagged / smart_money / whale / unknown.
  - label_wallets: batch lookup, sanctioned flag, fail-open on CH error.
  - wallet_histories: context aggregation (direction, avg size) and the
    FIFO smart-money round-trip heuristic.
  - enrich_feed_items: full attach, fail-open on broken client.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.routers.v1.whale_enrichment import (
    classify_labels,
    enrich_feed_items,
    label_wallets,
    stable_decimals,
    stable_usd,
    wallet_histories,
)

_USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
_TOKEN = "0xtoken"


# ── stable_usd / stable_decimals ──────────────────────────────────────


def test_stable_decimals_fallback_map():
    assert stable_decimals(_USDC) == 6
    assert stable_decimals(_USDT) == 6
    # DAI is 18
    dai = "0x6b175474e89094c44da98b954eedeac495271d0f"
    assert stable_decimals(dai) == 18


def test_stable_decimals_meta_overrides():
    assert stable_decimals(_USDC, 6) == 6
    assert stable_decimals(_USDC, 18) == 18  # meta wins over fallback
    assert stable_decimals(_USDC, None) == 6
    assert stable_decimals(_USDC, "garbage") == 6  # invalid → fallback


def test_stable_usd_raw_to_human():
    assert stable_usd(250_000_000_000, _USDC) == 250_000.0
    assert stable_usd(0, _USDC) == 0.0
    assert stable_usd(None, _USDC) == 0.0
    assert stable_usd("not-a-number", _USDC) == 0.0
    assert stable_usd(float("inf"), _USDC) == 0.0


# ── classify_labels ───────────────────────────────────────────────────


def test_classify_labels_exchange():
    labels = [{"name": "Binance 14", "category": "cex", "subtype": ""}]
    assert classify_labels(labels) == "exchange"


def test_classify_labels_flagged_beats_exchange():
    labels = [
        {"name": "Binance", "category": "cex", "subtype": ""},
        {"name": "Phisher", "category": "phish-hack", "subtype": ""},
    ]
    assert classify_labels(labels) == "flagged"


def test_classify_labels_smart_money_and_whale():
    assert classify_labels([{"name": "sniper bot", "category": "", "subtype": ""}]) == "smart_money"
    assert classify_labels([{"name": "whale", "category": "", "subtype": ""}]) == "whale"
    assert classify_labels([{"name": "Some DAO", "category": "defi", "subtype": ""}]) == "labeled"
    assert classify_labels([]) == "unknown"


# ── label_wallets ─────────────────────────────────────────────────────


class _RowsCH:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return self._rows


def test_label_wallets_batch():
    ch = _RowsCH(
        [
            ("0xsender", "Binance", "cex", "", "etherscan", 0),
            ("0xsender", "OFAC SDN", "sanctioned", "", "ofac", 1),
        ]
    )
    out = label_wallets(ch, ["0xsender"])
    assert out["0xsender"]["label_type"] == "flagged"
    assert out["0xsender"]["is_sanctioned"] is True
    assert len(out["0xsender"]["labels"]) == 2


def test_label_wallets_fail_open():
    class Boom:
        def execute(self, sql, params=None):
            raise RuntimeError("down")

    assert label_wallets(Boom(), ["0xsender"]) == {}
    assert label_wallets(None, ["0xsender"]) == {}


# ── wallet_histories: context + smart money ──────────────────────────


def _swap(ts, token0, token1, a0_in=0.0, a1_in=0.0, a0_out=0.0, a1_out=0.0):
    return (sender, "ethereum", ts, token0, token1, a0_in, a1_in, a0_out, a1_out)


sender = "0xsmart"


def test_wallet_histories_context_and_smart_money():
    now = datetime.now(tz=UTC)
    # Buy 100k of TOKEN with USDC, later sell for 150k → one winning trip.
    rows = [
        _swap(now - timedelta(days=10), _USDC, _TOKEN, a0_in=100_000_000_000),
        _swap(now - timedelta(days=5), _USDC, _TOKEN, a1_in=150_000_000_000),
    ]
    ch = _RowsCH(rows)
    out = wallet_histories(ch, [sender])
    entry = out[sender]
    assert entry["context"]["swap_count_30d"] == 2
    sm = entry["smart_money"]
    assert sm["round_trips"] == 1
    assert sm["wins"] == 1
    assert sm["win_rate"] == 1.0
    assert sm["realized_pnl_usd"] == 50_000.0
    assert sm["is_smart_money"] is False  # needs >= 3 trips


def test_smart_money_threshold():
    now = datetime.now(tz=UTC)
    rows = []
    for i in range(3):
        rows.append(_swap(now - timedelta(days=20 - i), _USDC, _TOKEN, a0_in=10_000_000))
        rows.append(_swap(now - timedelta(days=10 - i), _USDC, _TOKEN, a1_in=20_000_000))
    out = wallet_histories(_RowsCH(rows), [sender])
    sm = out[sender]["smart_money"]
    assert sm["round_trips"] == 3
    assert sm["wins"] == 3
    assert sm["is_smart_money"] is True


def test_wallet_histories_fail_open():
    class Boom:
        def execute(self, sql, params=None):
            raise RuntimeError("down")

    assert wallet_histories(Boom(), [sender]) == {}


# ── enrich_feed_items ─────────────────────────────────────────────────


def test_enrich_feed_items_full_attach():
    now = datetime.now(UTC)
    item = {
        "sender": "0xsmart",
        "usd_value": 500_000.0,
        "chain": "ethereum",
    }
    labels_rows = [("0xsmart", "sniper", "trader", "", "goplus", 0)]
    history_rows = []
    for i in range(3):
        history_rows.append(_swap(now - timedelta(days=20 - i), _USDC, _TOKEN, a0_in=10_000_000))
        history_rows.append(_swap(now - timedelta(days=10 - i), _USDC, _TOKEN, a1_in=20_000_000))

    class CH:
        def __init__(self):
            self.calls = 0

        def execute(self, sql, params=None):
            self.calls += 1
            if "wallet_labels" in sql:
                return labels_rows
            return history_rows

    out = enrich_feed_items(CH(), [item])
    assert out[0]["wallet_label"] == "smart_money"
    assert out[0]["is_smart_money"] is True
    assert out[0]["context"]["unusual"] is True  # 500k >> avg
    assert "smart-money wallet" in out[0]["wallet_context_line"]


def test_enrich_feed_items_fail_open_on_broken_client():
    class Boom:
        def execute(self, sql, params=None):
            raise RuntimeError("down")

    items = [{"sender": "0xa", "usd_value": 1.0}]
    out = enrich_feed_items(Boom(), items)
    assert out[0]["wallet_label"] == "unknown"
    assert out[0]["is_smart_money"] is False
    assert out[0]["context"]["unusual"] is False


def test_enrich_feed_items_empty():
    assert enrich_feed_items(None, []) == []
