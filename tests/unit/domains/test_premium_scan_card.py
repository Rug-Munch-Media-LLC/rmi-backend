"""Contract tests for the premium scan card (free / pro / alert).

The tests pin the three promises the card makes: a tier never shows more than it
is allowed to, the 4,096-character cap is enforced by re-rendering rather than
by cutting the string, and missing data renders as missing — never as zero.
"""

from __future__ import annotations

from app.domains.telegram.premium import extract, render_card, render_thumb, scan_keyboard
from app.domains.telegram.premium.keyboard import as_markup
from app.domains.telegram.premium.model import Cohort

SCAM = {
    "token_address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
    "token_symbol": "SCAM",
    "chain": "solana",
    "overall_risk": "critical",
    "rug_pull_probability": 88,
    "total_liquidity_usd": 3094.72,
    "top_10_concentration": 78.4,
    "total_holders": 412,
    "volume_24h": 41200,
    "price_usd": 0.000021,
    "price_change_24h": -38.2,
    "mint_authority": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
    "freeze_authority": None,
    "freeze_authority_renounced": True,
    "bundler_wallets": ["a", "b", "c", "d", "e", "f", "g"],
    "bundler_buy_volume_sol": 9.4,
    "bundler_percentage_of_supply": 34.2,
    "snipers": [
        {"buy_amount_sol": 2.0, "sold_percentage": 80.0, "pnl_percent": 120.0},
        {"buy_amount_sol": 1.5, "sold_percentage": 40.0, "pnl_percent": -20.0},
    ],
    "insiders_detected": ["x", "y"],
    "insider_buy_volume": 4.1,
    "liquidity_pools": [{"tvl_usd": 3094.72, "lp_locked_percentage": 0.0, "is_burned": False}],
    "dev_other_tokens": ["t1", "t2", "t3"],
    "dev_rug_history": 2,
    "dev_is_known_scammer": True,
    "critical_warnings": ["Honeypot simulation failed: sell reverts"],
    "risk_flags": ["CR03"],
    "scan_timestamp": "2026-10-23 14:02 UTC",
    "scan_duration_ms": 6100,
}

CLEAN = {
    "token_address": "MIMmint",
    "token_symbol": "MIM",
    "chain": "solana",
    "overall_risk": "low",
    "safety_score": 91,
    "total_liquidity_usd": 47220.24,
    "top_10_concentration": 12.0,
    "total_holders": 2803,
    "volume_24h": 485324,
    "price_usd": 0.0002964,
    "price_change_24h": 472,
    "mint_authority": None,
    "freeze_authority": None,
    "liquidity_pools": [{"tvl_usd": 47220.24, "lp_locked_percentage": 100, "is_burned": True}],
}

BUNDLED = dict(CLEAN, token_symbol="BUND", overall_risk="medium", top_10_concentration=91.7)


def test_free_tier_is_a_scan_at_a_glance() -> None:
    card = render_card(extract(SCAM), "free")
    assert card.chars < 700
    for needle in ("Verdict", "Liquidity", "Top-10", "Safety", "DexScreener"):
        assert needle in card.html
    assert "cohort" not in card.html.lower()


def test_pro_tier_carries_every_panel() -> None:
    card = render_card(extract(SCAM), "pro")
    for needle in (
        "init",
        "sold",
        "realised",
        "Bundle",
        "LP",
        "±1%",
        "Mint",
        "Freeze",
        "Prior rugs",
        "KOLs",
        "Flags",
    ):
        assert needle in card.html, needle
    assert card.chars > 900


def test_cap_re_renders_and_keeps_the_verdict() -> None:
    flooded = dict(SCAM, critical_warnings=[f"warning {i} " + "x" * 40 for i in range(120)])
    card = render_card(extract(flooded), "pro")
    assert card.chars <= 4096
    assert card.pruned
    assert "Verdict" in card.html
    assert "Liquidity" in card.html
    assert "DexScreener" in card.html


def test_plain_text_fallback_has_no_markup() -> None:
    card = render_card(extract(SCAM), "pro")
    assert "<b>" not in card.text
    assert "<pre>" not in card.text
    assert "<a " not in card.text
    assert "Liquidity" in card.text


def test_missing_data_is_never_zero() -> None:
    card = render_card(extract({"token_address": "x", "token_symbol": "NO"}), "free")
    assert "$0" not in card.html.split("Safety")[0]
    assert "unknown" in card.html


def test_tiers_differentiate_three_real_profiles() -> None:
    cards = [render_card(extract(p), "free") for p in (SCAM, BUNDLED, CLEAN)]
    heads = {c.html.splitlines()[0] for c in cards}
    assert len(heads) == 3
    assert "CRITICAL" in cards[0].html
    assert "MEDIUM" in cards[1].html
    assert "LOW" in cards[2].html


def test_alert_renders_the_delta_only() -> None:
    before = extract(SCAM)
    after = before.replace(
        cohorts=(Cohort("Snipers", 9, 12.4, 75.0, 4.81, True),),
        change_24h=-38.2,
    )
    card = render_card(
        after, "alert", before.replace(cohorts=(Cohort("Snipers", 9, 12.4, 10.0, 0.0, True),))
    )
    assert "exited 75.0%" in card.html
    assert "Liquidity" not in card.html
    assert card.chars < 700


def test_alert_without_history_never_invents_a_delta() -> None:
    card = render_card(extract(SCAM), "alert")
    assert "No prior snapshot" in card.html


def test_keyboard_fits_the_callback_limit() -> None:
    rows = scan_keyboard(SCAM["token_address"])
    assert [b["text"] for b in rows[0]] == ["Full scan", "Watch", "Chart"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in rows[0])
    assert as_markup(rows).inline_keyboard[0][0].text == "Full scan"


def test_thumbnail_is_a_png_at_photo_size(tmp_path) -> None:
    thumb = render_thumb(extract(SCAM), str(tmp_path))
    assert (thumb.width, thumb.height) == (960, 320)
    assert thumb.bytes_len > 5000
    with open(thumb.path, "rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_symbol_is_escaped_in_html() -> None:
    card = render_card(extract(dict(SCAM, token_symbol="<b>x</b>")), "free")
    assert "&lt;B&gt;X&lt;/B&gt;" in card.html
