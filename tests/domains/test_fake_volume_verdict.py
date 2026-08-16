"""Tests for the explicit FAKE VOLUME (wash trading) verdict.

Covers:
  - ``get_own_volume_authenticity`` computing a clear ``fake_volume_verdict``
    ("likely" / "organic") from the new cheap signals: buyer/seller-overlap
    self-deal count (same wallet buys AND sells) and the volume-to-holder
    churn ratio (high volume over a tiny holder base).
  - the v2 → legacy flatten carrying the full ``wash_trading`` block (and no
    longer mis-mapping ``fake_volume_pct`` into ``volume_real_ratio``).
  - the dedicated ``📉 Fake Volume`` verdict line rendering on BOTH /scan and
    /deepscan (key-metrics row): 🔴 Likely / 🟢 Organic / —.

No network, no Telegram, no real ClickHouse — ClickHouse is a fake client.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from app.domains.telegram.rugmunchbot.formatters import (
    format_deep_scan_report,
    format_scan_report,
)
from app.domains.telegram.rugmunchbot.formatters._scan_report_common import _fake_volume_verdict_text

# ═══════════════════════════════════════════════════════════════════════════
# Engine verdict (mocked ClickHouse)
# ═══════════════════════════════════════════════════════════════════════════


class _FakeCHClient:
    """Dispatch-on-SQL fake ClickHouse client.

    ``execute(sql, params)`` returns canned rows based on which table/query
    the engine sent — mirrors the real engine's query fan-out.
    """

    def __init__(self, stats: list, *, self_deals: list, transfer: list) -> None:
        self._stats = stats
        self._self_deals = self_deals
        self._transfer = transfer

    def execute(self, sql: str, params: dict[str, Any] | None = None, **_: Any) -> list:
        if "FROM wallet_memory.evm_transfers" in sql:
            return self._transfer
        if "GROUP BY tx_hash" in sql:
            # multi-swap txs (>=3 swaps in one tx)
            return [("0xwash", 3)]
        if "ORDER BY n DESC" in sql:
            # top-3 senders
            return [("0xa", 40), ("0xb", 25), ("0xc", 15)]
        if "HAVING buys > 3 AND sells > 3" in sql:
            # zero-net cycling (strict)
            return [("0xa", 8, 8)]
        if "HAVING buys >= 1 AND sells >= 1" in sql:
            # buyer/seller-overlap self-deals (any depth)
            return self._self_deals
        if "count() AS total_swaps" in sql:
            return self._stats
        return []


def _washy_client() -> _FakeCHClient:
    """A token whose volume is largely self-dealt over a tiny holder base."""
    # (total_swaps, unique_txs, self_swaps, vol0, vol1, unique_senders)
    stats = [(100, 60, 2, 1000.0, 2000.0, 12)]
    # (sender, buys, sells, swaps) — 3 wallets on BOTH sides of the book
    self_deals = [("0xa", 5, 4, 9), ("0xb", 3, 2, 5), ("0xc", 1, 1, 2)]
    # (transfer_count, recipient_holders, raw_value_sum) — tiny holder base
    transfer = [(30, 15, 5_000_000.0)]
    return _FakeCHClient(stats, self_deals=self_deals, transfer=transfer)


def _clean_client() -> _FakeCHClient:
    """A token with broad participation and no self-dealing."""
    stats = [(100, 90, 1, 1000.0, 2000.0, 40)]
    self_deals: list = []
    transfer = [(200, 500, 5_000_000.0)]  # 500 active recipients — not tiny
    return _FakeCHClient(stats, self_deals=self_deals, transfer=transfer)


async def _run_engine(client: _FakeCHClient, token: str) -> dict[str, Any]:
    """Run the real engine against a fake ClickHouse client."""
    from app.domains.scanner.engines import own_volume_authenticity as mod

    with (
        patch.object(mod, "_get_ch_client", return_value=client),
        patch.object(mod, "_load_pair_cache", return_value={}),
        patch.object(mod, "_get_pools_for_token", return_value=[]),
        patch.object(mod, "_normalize_amount", side_effect=lambda v, t, c: float(v)),
    ):
        return await mod.get_own_volume_authenticity(token, "ethereum", force_fresh=True)


async def test_engine_verdict_likely_with_self_deals() -> None:
    result = await _run_engine(_washy_client(), token="0x000000000000000000000000000000000000dead")
    assert result["result"] == "scanned"
    assert result["fake_volume_verdict"] == "likely"
    assert result["self_deal_wallets"] == 3
    assert result["self_deal_swaps"] == 16
    assert result["self_deal_swap_ratio"] == 0.16
    assert "3 self-deals" in result["fake_volume_detail"]
    # volume-to-holder churn: dex_volume 2000 / 15 holders = 133.33x
    assert result["active_holders"] == 15
    assert result["volume_to_holder_ratio"] == 133.33
    assert "volume 133x holders" in result["fake_volume_detail"]
    # the aggregate score must have been penalized below the clean band
    assert result["authentic_score"] < 70
    assert result["risk_level"] in ("medium", "high")
    # the signal is surfaced in the wash-signals list
    names = {s["name"] for s in result["wash_signals"]}
    assert "self_dealing_overlap" in names
    assert "volume_to_holder_churn" in names


async def test_engine_verdict_organic_when_clean() -> None:
    result = await _run_engine(_clean_client(), token="0x000000000000000000000000000000000000beef")
    assert result["result"] == "scanned"
    assert result["fake_volume_verdict"] == "organic"
    assert result["fake_volume_detail"] == ""
    assert result["self_deal_wallets"] == 0
    # broad participation keeps the authentic score in the clean band
    assert result["authentic_score"] >= 70
    assert result["risk_level"] == "low"
    names = {s["name"] for s in result["wash_signals"]}
    assert "self_dealing_overlap" not in names
    assert "volume_to_holder_churn" not in names


async def test_engine_verdict_none_when_data_insufficient() -> None:
    # <5 swaps → the engine degrades to unavailable (no verdict).
    client = _FakeCHClient([(3, 3, 0, 10.0, 20.0, 2)], self_deals=[], transfer=[])
    result = await _run_engine(client, token="0x000000000000000000000000000000000000cafe")
    assert result["result"] == "unavailable"
    assert result["fake_volume_verdict"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Flatten: v2 → legacy carries the full wash block
# ═══════════════════════════════════════════════════════════════════════════


def _v2_payload() -> dict[str, Any]:
    return {
        "token": "0xabc",
        "chain": "ethereum",
        "name": "Washy",
        "symbol": "WSH",
        "risk_flags": [],
        "green_flags": [],
        "safety_score": 40.0,
        "evidence": {
            "market": {
                "fake_volume_pct": 61.0,
                "authentic_score": 39,
                "risk_level": "high",
                "self_deal_wallets": 7,
                "self_deal_swaps": 41,
                "self_deal_swap_ratio": 0.12,
                "active_holders": 25,
                "volume_to_holder_ratio": 133.0,
                "fake_volume_verdict": "likely",
                "fake_volume_detail": "7 self-deals \u00b7 volume 133x holders",
                "confidence": 0.8,
                "wash_signals": [{"name": "self_dealing_overlap", "score": 0.12, "detail": "x"}],
            }
        },
    }


def test_v2_token_to_legacy_carries_wash_block_and_no_bogus_ratio() -> None:
    from app.domains.telegram.rugmunchbot.tools.scanner._flatten import v2_token_to_legacy

    legacy = v2_token_to_legacy(_v2_payload())
    wash = legacy["wash_trading"]
    assert wash["fake_volume_pct"] == 61.0
    assert wash["authentic_score"] == 39
    assert wash["self_deal_wallets"] == 7
    assert wash["volume_to_holder_ratio"] == 133.0
    assert wash["fake_volume_verdict"] == "likely"
    assert "7 self-deals" in wash["fake_volume_detail"]
    # The old mis-mapping (fake_volume_pct stashed into volume_real_ratio)
    # is gone — the legacy ratio slot stays None unless a real ratio exists.
    assert legacy["volume_real_ratio"] is None


def test_flatten_v2_carries_wash_block() -> None:
    from app.domains.telegram.rugmunchbot.tools.scanner._flatten import _flatten_v2

    flat = _flatten_v2(_v2_payload(), "0xabc", "ethereum")
    assert flat["wash_trading"]["self_deal_wallets"] == 7
    assert flat["wash_trading"]["fake_volume_verdict"] == "likely"


# ═══════════════════════════════════════════════════════════════════════════
# Formatter: dedicated 📉 Fake Volume line on /scan + /deepscan
# ═══════════════════════════════════════════════════════════════════════════


def _base_report() -> dict[str, Any]:
    return {
        "address": "0xabc123",
        "chain": "ethereum",
        "name": "Test Token",
        "symbol": "TST",
        "price": 1.0,
        "mcap": 1_000_000,
        "liquidity": 50_000,
        "volume_24h": 100_000,
        "price_change_1h": 1.0,
        "price_change_24h": 5.0,
        "price_change_7d": 10.0,
        "risk_score": 60,
        "honeypot": None,
        "contract_verified": None,
        "mintable": None,
        "owner_renounced": None,
        "lp_locked": None,
        "buy_tax": None,
        "sell_tax": None,
        "top_holders": [],
        "bundle_detected": False,
        "fresh_wallet_pct": 0,
        "volume_real_ratio": None,
        "dev_wallet": None,
        "socials": {},
        "threats": [],
    }


def test_scan_renders_likely_verdict_line() -> None:
    report = _base_report()
    report["wash_trading"] = {
        "fake_volume_pct": 61.0,
        "authentic_score": 39,
        "self_deal_wallets": 7,
        "self_deal_swaps": 41,
        "self_deal_swap_ratio": 0.12,
        "active_holders": 25,
        "volume_to_holder_ratio": 133.0,
        "fake_volume_verdict": "likely",
        "fake_volume_detail": "7 self-deals \u00b7 volume 133x holders",
    }
    out = format_scan_report(report)
    assert "\U0001f4c9 Fake Volume" in out  # 📉 Fake Volume
    assert "\U0001f534 <b>Likely</b>" in out  # 🔴 Likely
    assert "wash-trading detected: 7 self-deals \u00b7 volume 133x holders" in out


def test_deepscan_renders_likely_verdict_line() -> None:
    report = _base_report()
    report["wash_trading"] = {
        "fake_volume_pct": 55.0,
        "self_deal_wallets": 3,
        "self_deal_swap_ratio": 0.2,
        "fake_volume_verdict": "likely",
        "fake_volume_detail": "3 self-deals",
    }
    out = format_deep_scan_report(report)
    assert "\U0001f4c9 Fake Volume" in out
    assert "\U0001f534 <b>Likely</b>" in out
    assert "3 self-deals" in out


def test_scan_renders_organic_verdict() -> None:
    report = _base_report()
    report["wash_trading"] = {
        "fake_volume_pct": 5.0,
        "authentic_score": 95,
        "risk_level": "low",
        "self_deal_wallets": 0,
        "fake_volume_verdict": "organic",
        "fake_volume_detail": "",
    }
    out = format_scan_report(report)
    assert "\U0001f7e2 Organic volume patterns" in out
    assert "\U0001f534" not in out


def test_verdict_emdash_when_no_data() -> None:
    report = _base_report()
    out = format_scan_report(report)
    assert "\U0001f4c9 Fake Volume" in out
    assert "\u2014" in out.split("\U0001f4c9 Fake Volume")[1].split("\n")[0]
    # never claims "organic" without a check
    assert "Organic volume patterns" not in out


def test_legacy_percentage_fallback_verdict() -> None:
    # Legacy payload with only fake_volume_pct (no engine verdict) → derived.
    report = _base_report()
    report["wash_trading"] = {"fake_volume_pct": 22.0}
    assert "\U0001f534 <b>Likely</b>" in _fake_volume_verdict_text(report)
    report["wash_trading"] = {"fake_volume_pct": 5.0}
    assert "Organic volume patterns" in _fake_volume_verdict_text(report)


def test_churn_note_appended_when_volume_up_price_flat() -> None:
    report = _base_report()
    report["volume_24h"] = 500_000
    report["price_change_24h"] = 0.5
    report["wash_trading"] = {
        "fake_volume_pct": 61.0,
        "self_deal_wallets": 2,
        "self_deal_swap_ratio": 0.1,
        "fake_volume_verdict": "likely",
        "fake_volume_detail": "2 self-deals",
    }
    out = format_scan_report(report)
    assert "2 self-deals" in out
    assert "vol up, price flat" in out


def test_churn_note_never_fires_verdict_without_engine_data() -> None:
    # High volume + flat price alone (no wash_trading payload) → "—", honest.
    report = _base_report()
    report["volume_24h"] = 500_000
    report["price_change_24h"] = 0.5
    assert _fake_volume_verdict_text(report) == "\u2014"
