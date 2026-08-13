"""Unit tests for the degodded ``handlers/_scanning_helpers.py`` module.

Covers the pure DexScreener pair normalization, completed-scan merge,
TA card rendering and deployer risk-bucket helpers extracted from
``handlers/scanning.py``. No network I/O.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.handlers._scanning_helpers import (
    _DEPLOYER_BUCKET_EMOJI,
    _bucket_emoji,
    _ds_created_iso,
    _ds_pairs,
    _merge_completed_scan,
    _ta_arrow,
    _ta_rsi_emoji,
    _ta_vol_arrow,
    format_ta_card,
)

# ── DexScreener pair normalization ─────────────────────────────────────────


def test_ds_pairs_accepts_dict_with_pairs() -> None:
    data = {"pairs": [{"address": "0x1"}, {"address": "0x2"}]}
    assert _ds_pairs(data) == [{"address": "0x1"}, {"address": "0x2"}]


def test_ds_pairs_accepts_bare_list_and_filters_non_dicts() -> None:
    assert _ds_pairs([{"a": 1}, "junk", 42]) == [{"a": 1}]


def test_ds_pairs_empty_shapes() -> None:
    assert _ds_pairs({"pairs": []}) == []
    assert _ds_pairs(None) == []
    assert _ds_pairs("nope") == []


def test_ds_created_iso_converts_ms_epoch() -> None:
    assert _ds_created_iso({"pairCreatedAt": 1700000000000}) == "2023-11-14T22:13:20+00:00"


def test_ds_created_iso_none_for_invalid() -> None:
    assert _ds_created_iso({}) is None
    assert _ds_created_iso({"pairCreatedAt": 0}) is None
    assert _ds_created_iso({"pairCreatedAt": -5}) is None
    assert _ds_created_iso({"pairCreatedAt": "garbage"}) is None
    assert _ds_created_iso({"pairCreatedAt": None}) is None


# ── completed-scan merge ───────────────────────────────────────────────────


def test_merge_completed_scan_fills_empty_keys() -> None:
    result = {"wallet_metrics": {}, "evidence": {}}
    completed = {
        "wallet_metrics": {"holders": 10},
        "bundle_analysis": {"flagged": True},
        "funding_analysis": {"check": "funding_sources"},
        "deployer_profile": {"risk": "LOW"},
        "evidence": {"contract": {"is_verified": True}},
    }
    _merge_completed_scan(result, completed)
    assert result["wallet_metrics"] == {"holders": 10}
    assert result["bundle_analysis"] == {"flagged": True}
    # funding_analysis is filled in both top-level and evidence slots
    assert result["funding_analysis"] == {"check": "funding_sources"}
    assert result["evidence"]["funding_analysis"] == {"check": "funding_sources"}
    assert result["deployer_profile"] == {"risk": "LOW"}
    assert result["evidence"]["contract"] == {"is_verified": True}


def test_merge_completed_scan_never_overwrites() -> None:
    result = {"wallet_metrics": {"existing": True}, "evidence": {}}
    completed = {"wallet_metrics": {"new": True}, "evidence": {"contract": {"x": 1}}}
    _merge_completed_scan(result, completed)
    assert result["wallet_metrics"] == {"existing": True}
    assert result["evidence"]["contract"] == {"x": 1}


def test_merge_completed_scan_probes_deep_profile() -> None:
    result: dict = {}
    completed = {"deep_profile": {"deployer_profile": {"risk": "HIGH"}}}
    _merge_completed_scan(result, completed)
    assert result["deployer_profile"] == {"risk": "HIGH"}


def test_merge_completed_scan_skips_falsy_completed_values() -> None:
    result = {"evidence": {}}
    completed = {"wallet_metrics": None, "evidence": {"empty_field": None}}
    _merge_completed_scan(result, completed)
    assert "wallet_metrics" not in result
    assert result["evidence"] == {}


# ── TA emoji helpers ───────────────────────────────────────────────────────


def test_ta_emojis() -> None:
    assert _ta_arrow("BULLISH") == "🟢"
    assert _ta_arrow("BEARISH") == "🔴"
    assert _ta_arrow("NEUTRAL") == "⚪"
    assert _ta_arrow("BOGUS") == "⚪"
    assert _ta_rsi_emoji("Overbought") == "🔴"
    assert _ta_rsi_emoji("Oversold") == "🟢"
    assert _ta_rsi_emoji("Neutral") == "⚪"
    assert _ta_vol_arrow("Rising") == "📈"
    assert _ta_vol_arrow("Declining") == "📉"
    assert _ta_vol_arrow("Steady") == "\u2796"
    assert _ta_vol_arrow("???") == "\u2796"


def _full_analysis() -> dict:
    return {
        "trend": "BULLISH",
        "rsi": 72.3,
        "rsi_label": "Overbought",
        "ema9": 1.1,
        "ema21": 1.0,
        "ema_cross": "BULLISH CROSS",
        "macd_line": 0.001,
        "macd_label": "Bullish",
        "volume_ratio": 2.5,
        "volume_trend": "Rising",
        "support": 0.9,
        "resistance": 1.3,
        "candles": 100,
    }


def _full_meta() -> dict:
    return {
        "name": "Test",
        "symbol": "TST",
        "address": "0xabc",
        "chain": "ethereum",
        "price": 1.25,
        "change_24h": 3.4,
        "rug_score": 75,
        "rug_level": "DANGER",
    }


def test_format_ta_card_full() -> None:
    text = format_ta_card(_full_analysis(), _full_meta())
    assert "<b>Technical Analysis</b>" in text
    assert "<b>Test</b> (TST)" in text
    assert "<code>0xabc</code>" in text
    assert "Ethereum · 1h · 100 candles" in text
    assert "<b>Trend: BULLISH</b> 🟢" in text
    assert "72.3" in text
    assert "Overbought" in text
    assert "EMA-9: $1.1000 | EMA-21: $1.0000" in text
    assert "BULLISH CROSS" in text
    assert "MACD: 🟢 <b>Bullish</b> (+0.001)" in text
    assert "2.5\u00d7 avg (Rising)" in text
    assert "Resistance: $1.3000" in text
    assert "Support:    $0.9000" in text
    assert "Rug Risk" in text
    assert "DANGER" in text
    assert "75/100" in text
    assert "financial advice" in text


def test_format_ta_card_sparse_meta() -> None:
    text = format_ta_card({}, {})
    assert "<b>Unknown</b> (???)" in text
    assert "RSI-14: N/A" in text
    assert "Volume: \u2796 Steady" in text
    assert "Rug Risk" not in text
    assert "Trend: NEUTRAL" in text


def test_format_ta_card_no_price_no_change() -> None:
    text = format_ta_card({}, {"name": "X", "symbol": "Y"})
    assert "Price:" not in text


def test_format_ta_card_bearish_variants() -> None:
    analysis = _full_analysis()
    analysis.update(
        {"trend": "BEARISH", "ema_cross": "BEARISH CROSS", "macd_label": "Bearish", "volume_trend": "Declining"}
    )
    meta = _full_meta()
    meta["change_24h"] = -1.5
    meta["rug_level"] = "SAFE"
    text = format_ta_card(analysis, meta)
    assert "Trend: BEARISH" in text
    assert "🔴" in text
    assert "BEARISH CROSS" in text
    assert "Volume: 📉" in text


# ── deployer bucket emoji ──────────────────────────────────────────────────


def test_bucket_emoji() -> None:
    assert _bucket_emoji("NEW") == _DEPLOYER_BUCKET_EMOJI["NEW"]
    assert _bucket_emoji("new") == _DEPLOYER_BUCKET_EMOJI["NEW"]  # case-insensitive
    assert _bucket_emoji("FARM") == "🌱"
    assert _bucket_emoji("REPEAT_RUG") == "🔴"
    assert _bucket_emoji("PROLIFIC") == "💰"
    assert _bucket_emoji("UNKNOWN") == "🏷️"
