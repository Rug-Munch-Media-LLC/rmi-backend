"""Unit tests for the degodded ``handlers/_market_helpers.py`` module.

Covers the pure /trending formatting, merge, sort, render and keyboard
helpers extracted from ``handlers/market.py``. No network, no DB — only
telegram keyboard primitives and the formatter helpers.

Coverage targets:
  - _time_ago / _resolve_chain / _chain_badge / _age_badge / _liq_badge
  - _risk_emoji / _normalize_address
  - _merge_trending_sources (dedupe, quality ranking, unknown filtering)
  - _clean_token_name / _trending_display
  - _window_change / _sort_trending / _window_note
  - _render_trending_text / _build_trending_kb
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domains.telegram.rugmunchbot.handlers._market_helpers import (
    AGE_BADGES,
    CHAIN_BADGES,
    LIQ_TIERS,
    RUGCHARTS_CHAIN_ALIASES,
    TRENDING_CHAINS,
    TRENDING_WINDOWS,
    _age_badge,
    _build_trending_kb,
    _chain_badge,
    _clean_token_name,
    _liq_badge,
    _merge_trending_sources,
    _normalize_address,
    _render_trending_text,
    _resolve_chain,
    _risk_emoji,
    _sort_trending,
    _time_ago,
    _trending_display,
    _window_change,
    _window_note,
)


def test_time_ago_empty() -> None:
    assert _time_ago(None) == ""
    assert _time_ago("") == ""


def test_time_ago_seconds_minutes_hours_days() -> None:
    import re

    # deltas are bucket midpoints so a few seconds of test jitter can't
    # cross a bucket boundary; assert the bucket + range, not exact ints
    assert _time_ago((datetime.now(UTC) - timedelta(seconds=30)).isoformat()) == "30s ago"
    m = re.match(r"^(\d+)m ago$", _time_ago((datetime.now(UTC) - timedelta(minutes=5)).isoformat()))
    assert m is not None
    assert 4 <= int(m.group(1)) <= 5
    h = re.match(r"^(\d+)h ago$", _time_ago((datetime.now(UTC) - timedelta(hours=3)).isoformat()))
    assert h is not None
    assert 2 <= int(h.group(1)) <= 3
    d = re.match(r"^(\d+)d ago$", _time_ago((datetime.now(UTC) - timedelta(days=5)).isoformat()))
    assert d is not None
    assert 4 <= int(d.group(1)) <= 5


def test_time_ago_future_is_just_now() -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert _time_ago(future) == "just now"


def test_time_ago_zulu_suffix_and_invalid() -> None:
    # "Z" suffix is normalized before fromisoformat
    zulu = (datetime.now(UTC) - timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
    assert _time_ago(zulu).endswith("m ago")


def test_time_ago_malformed_iso_raises_value_error() -> None:
    # [REVIEW] _time_ago only guards (ConnectionError, OSError, TimeoutError);
    # ValueError from a malformed ISO string escapes to the caller.
    with pytest.raises(ValueError, match="Invalid isoformat string"):
        _time_ago("not-a-date")


# ── chain resolution / badges ──────────────────────────────────────────────


def test_resolve_chain_aliases() -> None:
    assert _resolve_chain(None) == "solana"
    assert _resolve_chain("") == "solana"
    assert _resolve_chain("SOL") == "solana"
    assert _resolve_chain("ETH") == "ethereum"
    assert _resolve_chain(" bnb ") == "bsc"
    assert _resolve_chain("Fantom") == "fantom"  # unknown passes through lowercased


def test_chain_alias_map_and_constant() -> None:
    assert RUGCHARTS_CHAIN_ALIASES == {
        "sol": "solana",
        "eth": "ethereum",
        "bnb": "bsc",
        "arb": "arbitrum",
        "tron": "tron",
    }
    assert "solana" in TRENDING_CHAINS
    assert "arbitrum" in TRENDING_CHAINS


def test_chain_badge() -> None:
    assert _chain_badge("solana") == CHAIN_BADGES["solana"]
    assert _chain_badge("SOLANA") == CHAIN_BADGES["solana"]  # case-insensitive
    assert _chain_badge("unknown-chain") == "⚪"


def test_age_badge_thresholds() -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    assert _age_badge(None) == "❓"
    assert _age_badge(0) == "❓"
    assert _age_badge(now_ms) == "🆕"  # < 24h
    assert _age_badge(now_ms - 24 * 3600 * 1000 - 1) == "🌱"  # < 7d
    assert _age_badge(now_ms - 168 * 3600 * 1000 - 1) == "🌿"  # < 30d
    assert _age_badge(now_ms - 720 * 3600 * 1000 - 1) == "🌳"  # > 30d


def test_age_badges_table_shape() -> None:
    assert AGE_BADGES[0][0] == 24
    assert AGE_BADGES[-1][0] == float("inf")


def test_liq_badge_tiers() -> None:
    assert _liq_badge(5_000_000) == "💧 Deep"
    assert _liq_badge(100_000) == "💦 Medium"
    assert _liq_badge(50_000) == "💧 Shallow"
    assert _liq_badge(10_000) == "💧 Shallow"  # boundary inclusive
    assert _liq_badge(9_999) == "⚠️ Critical"
    assert _liq_badge(-5) == "⚠️ Critical"
    assert LIQ_TIERS[0] == (1_000_000, "💧 Deep")
    assert LIQ_TIERS[-1] == (0, "⚠️ Critical")


def test_risk_emoji() -> None:
    assert _risk_emoji(None) == "⚪"
    assert _risk_emoji(90) == "🔴"
    assert _risk_emoji(70) == "🔴"  # boundary inclusive
    assert _risk_emoji(69) == "🟡"
    assert _risk_emoji(40) == "🟡"
    assert _risk_emoji(39) == "🟢"
    assert _risk_emoji("75") == "🔴"  # numeric strings accepted
    assert _risk_emoji("garbage") == "⚪"


def test_normalize_address() -> None:
    assert _normalize_address("  0xABC  ") == "0xabc"
    assert _normalize_address("0xDEF") == "0xdef"


# ── merge / dedupe ─────────────────────────────────────────────────────────


def test_merge_trending_dedupes_by_address_preferring_quality() -> None:
    rugcharts = [
        {
            "address": "0xaaa",
            "name": "A Coin",
            "symbol": "AC",
            "volume_24h": 100,
            "liquidity_usd": 1000,
            "source": "rugcharts",
        }
    ]
    dexscreener = [
        {
            "address": "0xAAA",  # same address, different case
            "name": "A Coin",
            "symbol": "AC",
            "volume_24h": 10,
            "liquidity_usd": 100,
            "source": "dexscreener",
        }
    ]
    merged = _merge_trending_sources(rugcharts, dexscreener, None)
    assert len(merged) == 1
    assert merged[0]["_source"] == "rugcharts"
    assert merged[0]["_priority"] == 3
    assert merged[0]["_quality"] == 8  # name(2)+symbol(1)+vol(1)+liq(1)+priority(3)


def test_merge_trending_replaces_lower_quality_entry() -> None:
    low = [{"address": "0xaaa", "name": "Unknown", "symbol": "???", "source": "dexscreener"}]
    high = [
        {
            "address": "0xaaa",
            "name": "Named",
            "symbol": "NM",
            "volume_24h": 5,
            "liquidity_usd": 5,
            "source": "coingecko",
        }
    ]
    merged = _merge_trending_sources(None, low, high)
    assert len(merged) == 1
    assert merged[0]["name"] == "Named"
    assert merged[0]["_source"] == "coingecko"


def test_merge_trending_filters_unknown_names() -> None:
    tokens = [{"address": "0xbbb", "name": "Unknown", "symbol": "???", "source": "dexscreener"}]
    assert _merge_trending_sources([], tokens, []) == []


def test_merge_trending_drops_addressless_entries() -> None:
    """CoinGecko-style entries (coingecko_id only, NO contract address) are
    dropped — they would otherwise render as '[address not verified]' and
    crowd out real meme movers."""
    cg = [
        {"coingecko_id": "btc", "name": "Bitcoin", "symbol": "BTC", "volume_24h": 1000, "source": "coingecko"},
        {"coingecko_id": "eth", "name": "Ether", "symbol": "ETH", "volume_24h": 900, "source": "coingecko"},
        {"coingecko_id": "sol", "name": "Solana", "symbol": "SOL", "volume_24h": 800, "source": "coingecko"},
    ]
    assert _merge_trending_sources(None, None, cg, limit=2) == []


def test_merge_trending_fresh_launch_limit_and_priority() -> None:
    """Fresh-launch (token-profiles) entries carry real addresses and rank
    at the same priority as RugCharts — a newly deployed meme beats an old
    infra token with the same data quality."""
    fresh = [
        {"address": "0xf1", "name": "PumpFresh", "symbol": "PMP", "created_at": 0, "source": "dexscreener:profiles"},
        {"address": "0xf2", "name": "MemeNew", "symbol": "NEW", "created_at": 0, "source": "dexscreener:profiles"},
        {"address": "0xf3", "name": "RugJuice", "symbol": "RUG", "created_at": 0, "source": "dexscreener:profiles"},
    ]
    merged = _merge_trending_sources(None, None, fresh, limit=2)
    assert len(merged) == 2
    assert all(t.get("address") for t in merged)  # real addresses only


def test_merge_trending_sorts_by_quality_then_volume() -> None:
    a = {"address": "0x1", "name": "Vol", "symbol": "V", "volume_24h": 999, "source": "dexscreener"}
    b = {"address": "0x2", "name": "Liq", "symbol": "L", "volume_24h": 0, "liquidity_usd": 10, "source": "dexscreener"}
    merged = _merge_trending_sources([], [a, b], [])
    assert merged[0]["name"] == "Vol"  # vol wins over liq at same priority


def test_merge_trending_none_inputs() -> None:
    assert _merge_trending_sources(None, None, None) == []


def test_merge_trending_fresh_launch_and_mover_bonuses() -> None:
    """Fresh launches (new) and big |24h %| movers outrank stale flat tokens:
    freshness bonus (+2 <24h) and mover bonus (+2 >=100%) are added to the
    quality score, so a new pump beats an old quiet token with equal data."""
    old_flat = {
        "address": "0xold",
        "name": "OldCoin",
        "symbol": "OLD",
        "volume_24h": 500_000,
        "liquidity_usd": 1_000_000,
        "change_24h": 0.5,
        "created_at": 30 * 24 * 3600 * 1000,  # 30d old
        "source": "dexscreener",
    }
    new_pump = {
        "address": "0xnew",
        "name": "FreshPump",
        "symbol": "FRESH",
        "volume_24h": 100_000,
        "liquidity_usd": 100_000,
        "change_24h": 250.0,
        "created_at": 2 * 3600 * 1000,  # 2h old
        "source": "dexscreener:profiles",
    }
    merged = _merge_trending_sources(None, [old_flat], [new_pump], limit=2)
    assert len(merged) == 2
    # fresh + mover bonuses lift the new pump above the old flat coin
    assert merged[0]["address"] == "0xnew"


# ── name cleaning ──────────────────────────────────────────────────────────


def test_clean_token_name_garbage_patterns() -> None:
    assert _clean_token_name("PEPE on robinhood right now", "PEPE") == ("PEPE", "PEPE")
    # "crawled out of..." is stripped; the remainder "a coin" survives because
    # the symbol is contained within it (symbol-in-name prefers the name)
    assert _clean_token_name("a coin crawled out of the mud", "COIN") == ("a coin", "COIN")


def test_clean_token_name_sentence_fallback() -> None:
    long = "This is a very long token name that goes on and on and on"
    assert _clean_token_name(long, "TKN") == ("TKN", "TKN")
    assert _clean_token_name("Unknown", "SYM") == ("SYM", "SYM")
    assert _clean_token_name("", "SYM") == ("SYM", "SYM")
    assert _clean_token_name("", "") == ("Unknown", "???")


def test_clean_token_name_keeps_real_names() -> None:
    assert _clean_token_name("Bonk", "BONK") == ("Bonk", "BONK")
    assert _clean_token_name("Pepe", "PEPE") == ("Pepe", "PEPE")  # symbol in name prefers name


def test_trending_display_skips_address_like_names() -> None:
    assert _trending_display({"name": "0xdeadbeef", "symbol": "ZZ"}) == ("ZZ", "ZZ")
    long_addr = "a" * 44
    assert _trending_display({"name": long_addr, "symbol": "ZZ"}) == ("ZZ", "ZZ")
    assert _trending_display({"name": "Bonk", "symbol": "BONK"}) == ("Bonk", "BONK")


# ── windows / sorting ──────────────────────────────────────────────────────


def test_window_change_fallbacks() -> None:
    assert _window_change({"change_1h": 1.5}, "1h") == 1.5
    assert _window_change({"change_7d": 7.5}, "7d") == 7.5
    assert _window_change({}, "7d") == 0.0  # falls back to 24h → 0
    assert _window_change({"change_24h": 3.0}, "24h") == 3.0
    assert _window_change({"change_7d": "garbage"}, "7d") == 0.0
    assert _window_change({"change_1h": None}, "1h") == 0.0


def test_sort_trending_windows() -> None:
    tokens = [
        {"name": "a", "volume_24h": 5, "change_24h": 1.0, "change_7d": 2.0},
        {"name": "b", "volume_24h": 50, "change_24h": 3.0, "change_7d": 1.0},
        {"name": "c", "volume_24h": 10, "change_24h": 2.0},
    ]
    assert [t["name"] for t in _sort_trending(tokens, "1h")] == ["b", "c", "a"]
    assert [t["name"] for t in _sort_trending(tokens, "24h")] == ["b", "c", "a"]
    # 7d only when any token has change_7d
    assert [t["name"] for t in _sort_trending(tokens, "7d")] == ["a", "b", "c"]
    no_7d = [{"name": "a", "change_24h": 1.0}, {"name": "b", "change_24h": 3.0}]
    assert [t["name"] for t in _sort_trending(no_7d, "7d")] == ["b", "a"]


def test_sort_trending_is_mutation_free() -> None:
    tokens: list[dict[str, Any]] = [{"name": "a", "volume_24h": 5}, {"name": "b", "volume_24h": 50}]
    original = list(tokens)
    _sort_trending(tokens, "1h")
    assert tokens == original


def test_sort_trending_surfaces_losers_both_directions() -> None:
    """Degen ranking: biggest |24h %| movers win — juicy red rugs surface
    alongside green pumps, not just gainers."""
    tokens = [
        {"name": "Pump", "change_24h": 500.0},
        {"name": "Rug", "change_24h": -90.0},
        {"name": "Flat", "change_24h": 1.0},
        {"name": "MidGreen", "change_24h": 40.0},
        {"name": "MidRed", "change_24h": -45.0},
    ]
    order = [t["name"] for t in _sort_trending(tokens, "24h")]
    assert order[0] == "Pump"  # +500% first
    assert order[1] == "Rug"  # -90% dump is the next biggest mover
    # both a negative and a positive mover make the top ranks
    assert "MidRed" in order[:4]
    assert "MidGreen" in order[:4]
    assert order[-1] == "Flat"  # flat token sinks


def test_sort_trending_7d_surfaces_losers() -> None:
    tokens = [
        {"name": "Green7d", "change_7d": 80.0},
        {"name": "Red7d", "change_7d": -75.0},
        {"name": "NoChange", "change_7d": 0.0},
    ]
    order = [t["name"] for t in _sort_trending(tokens, "7d")]
    assert order[:2] == ["Green7d", "Red7d"]


def test_window_note() -> None:
    assert "ranked by volume" in _window_note("1h", [])
    assert "using 24h deltas" in _window_note("7d", [{"name": "a"}])
    assert _window_note("7d", [{"change_7d": 1.0}]) == ""
    assert _window_note("24h", []) == ""


# ── rendering ──────────────────────────────────────────────────────────────


def test_render_trending_text_basic() -> None:
    tokens = [
        {
            "address": "0xabc",
            "name": "Test Coin",
            "symbol": "TC",
            "volume_24h": 1000,
            "liquidity_usd": 50000,
            "change_24h": 5.5,
            "risk_score": 50,
        }
    ]
    text = _render_trending_text(tokens, "solana", "24h")
    assert "Trending — Solana · 24h" in text
    assert "<b>Test Coin</b> (TC)" in text
    assert "5.50%" in text
    assert "0xabc" in text
    assert "Chains:" in text


def test_render_trending_text_skips_missing_address_and_caps_8() -> None:
    tokens = [
        {"address": "0x1", "name": "One", "symbol": "1"},
        {"address": "", "name": "NoAddr", "symbol": "NA"},
        {"address": "0x2", "name": "Two", "symbol": "2"},
    ] + [{"address": f"0x{i}", "name": f"N{i}", "symbol": f"{i}"} for i in range(3, 20)]
    text = _render_trending_text(tokens, "ethereum", "1h")
    assert "NoAddr" not in text
    # rendered counter increments only for address-bearing tokens
    assert text.count("\n1.") == 1  # first rendered line is numbered 1 (NoAddr skipped)


def test_render_trending_text_unknown_name_truncated_address() -> None:
    tokens = [{"address": "0x0123456789abcdef0123456789abcdef0123456789abcdef", "name": "", "symbol": ""}]
    text = _render_trending_text(tokens, "ethereum", "24h")
    assert "0x012345...abcdef" in text


def test_render_trending_text_bundle_flag() -> None:
    tokens = [{"address": "0xabc", "name": "Ruggy", "symbol": "RG", "bundle_detected": True}]
    text = _render_trending_text(tokens, "solana", "24h")
    assert "🚨" in text


def test_render_trending_text_window_note() -> None:
    text = _render_trending_text([], "solana", "1h")
    assert "ranked by volume" in text


# ── keyboard ───────────────────────────────────────────────────────────────


def test_build_trending_kb_structure() -> None:
    tokens = [{"address": "0xaaa", "name": "A Coin", "symbol": "AC"}, {"address": "0xbbb", "name": "B", "symbol": "B"}]
    kb = _build_trending_kb(tokens, "solana", "24h")
    rows = kb.inline_keyboard
    # 2 token rows + 2 chain rows (3 per row for 5 chains) + 1 window + 1 refresh
    assert len(rows) == 6
    assert rows[0][0].text == "🔍 A Coin (AC)"
    assert rows[0][0].callback_data == "scan_0xaaa_solana"
    # chain selector marks the active chain with brackets
    chain_callbacks = [btn.callback_data for row in rows[2:4] for btn in row]
    assert "trending_chain_solana" in chain_callbacks
    sol_btn = next(btn for row in rows[2:4] for btn in row if btn.callback_data == "trending_chain_solana")
    assert sol_btn.text.startswith("[")
    win_callbacks = [btn.callback_data for row in rows for btn in row if btn.callback_data.startswith("trending_win")]
    assert win_callbacks == [f"trending_win_solana_{w}" for w in TRENDING_WINDOWS]
    # refresh + menu row
    assert rows[-1][0].callback_data == "trending_refresh_solana"
    assert rows[-1][1].callback_data == "menu_main"


def test_build_trending_kb_unknown_label_uses_address() -> None:
    tokens = [{"address": "0x0123456789abcdef0123456789abcdef0123456789abcdef", "name": "Unknown", "symbol": "???"}]
    kb = _build_trending_kb(tokens, "solana", "24h")
    assert kb.inline_keyboard[0][0].text == "🔍 0x012345...abcdef"
