"""Unit tests for the trending-token own-data enrichment helper.

Covers ``app/routers/chat/_trending_enrichment.py``:

  - Render helpers: compact counts, deployer-age days, short addresses.
  - ``_render_line``: builds "our data: …" from real engine signals only and
    returns "" when a token has no own-data signals (no fabricated numbers).
  - ``enrich_trending_lines``: resolves the surfaced trending symbols to OUR
    indexed tokens and enriches them with mocked engines.
  - Fail-open: ClickHouse/upstream down → [] (external-only, no noise).
  - Wiring: ``build_market_context_block`` appends the "Our data" line when
    enrichment succeeds and still emits the core block when it does not.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.routers.chat import _market_context as mc, _trending_enrichment as te
from app.routers.market_intel import cache as market_cache

TRENDING_FIXTURE = {
    "coins": [
        {"item": {"symbol": "pepe"}},
        {"item": {"symbol": "wif"}},
        {"item": {"symbol": "jup"}},
    ]
}

FULL_SIGNALS = {
    "holder_count": 1204,
    "top5_pct": 62.0,
    "deployer_age_days": 14.2,
    "prior_tokens": 3,
    "is_bundled": True,
    "volume_24h": 3_200_000,
}

EMPTY_SIGNALS: dict = {}


@pytest.fixture(autouse=True)
def _clear_market_cache() -> None:
    market_cache._cache.clear()
    yield
    market_cache._cache.clear()


# ── Pure render helpers ───────────────────────────────────────────────


def test_fmt_count_compact() -> None:
    assert te._fmt_count(42) == "42"
    assert te._fmt_count(1234) == "1.2k"
    assert te._fmt_count(12_400) == "12.4k"
    assert te._fmt_count(3_200_000) == "3.2m"


def test_fmt_age_days() -> None:
    assert te._fmt_age_days(0.4) == "<1d"
    assert te._fmt_age_days(14.2) == "14d"
    assert te._fmt_age_days(30.0) == "30d"


def test_short_addr() -> None:
    assert te._short_addr("0x1234567890abcdef1234567890abcdef12345678") == "0x1234…5678"
    assert te._short_addr("short") == "short"


# ── Rendering (real signals only) ─────────────────────────────────────


def test_render_line_full_signals() -> None:
    line = te._render_line("PEPE", "0x1234567890abcdef1234567890abcdef12345678", FULL_SIGNALS)
    assert line.startswith("PEPE (0x1234…5678): our data: ")
    assert "1.2k holders" in line
    assert "top5 62%" in line
    assert "deployer 14d" in line
    assert "3 prior tokens" in line
    assert "bundle detected" in line
    assert "dex vol 3.2m" in line


def test_render_line_empty_signals_returns_empty() -> None:
    assert te._render_line("PEPE", "0x1234567890abcdef1234567890abcdef12345678", EMPTY_SIGNALS) == ""


def test_render_line_partial_signals_only_real() -> None:
    line = te._render_line("WIF", "addr", {"holder_count": 0, "is_bundled": True, "volume_24h": 0})
    assert "0 holders" not in line
    assert "dex vol 0" not in line
    assert "bundle detected" in line


# ── enrich_trending_lines (mocked engines) ────────────────────────────


async def test_enrich_trending_lines_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.market_intel.providers._cg", AsyncMock(return_value=TRENDING_FIXTURE))
    monkeypatch.setattr(
        te,
        "_resolve_indexed_token",
        AsyncMock(
            side_effect=[
                {"token_address": "0xaa11", "chain": "ethereum"},
                {"token_address": "0xbb22", "chain": "solana"},
                None,  # JUP not in our index → skipped, no fabrication
            ]
        ),
    )
    monkeypatch.setattr(
        te,
        "_enrich_token_bounded",
        AsyncMock(return_value=dict(FULL_SIGNALS)),
    )

    lines = await te.enrich_trending_lines(limit=5)
    assert len(lines) == 2
    assert "PEPE" in lines[0]
    assert "our data: 1.2k holders" in lines[0]
    assert "WIF" in lines[1]


async def test_enrich_trending_lines_fail_open_when_trending_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.market_intel.providers._cg", AsyncMock(return_value=None))
    assert await te.enrich_trending_lines() == []


async def test_enrich_trending_lines_fail_open_when_no_index_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.market_intel.providers._cg", AsyncMock(return_value=TRENDING_FIXTURE))
    monkeypatch.setattr(te, "_resolve_indexed_token", AsyncMock(return_value=None))
    assert await te.enrich_trending_lines() == []


async def test_enrich_trending_lines_fail_open_on_engine_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.market_intel.providers._cg", AsyncMock(return_value=TRENDING_FIXTURE))
    monkeypatch.setattr(
        te,
        "_resolve_indexed_token",
        AsyncMock(return_value={"token_address": "0xaa11", "chain": "ethereum"}),
    )
    monkeypatch.setattr(te, "_enrich_token_bounded", AsyncMock(side_effect=RuntimeError("CH down")))
    assert await te.enrich_trending_lines() == []


# ── Wiring into the market context block ──────────────────────────────


async def test_build_market_context_block_appends_our_data_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The enrichment line lands in the block when our data is available."""
    monkeypatch.setattr(
        mc,
        "_cg",
        AsyncMock(
            side_effect=[
                {
                    "bitcoin": {"usd": 67234.5, "usd_24h_change": 1.8},
                    "ethereum": {"usd": 3521.25, "usd_24h_change": 2.3},
                },
                TRENDING_FIXTURE,
            ]
        ),
    )
    monkeypatch.setattr(
        mc, "_fng", AsyncMock(return_value={"data": [{"value": "68", "value_classification": "Greed"}]})
    )
    monkeypatch.setattr(
        mc, "_fetch_enrichment_lines", AsyncMock(return_value=["PEPE (0x1234…5678): our data: 1.2k holders"])
    )

    block = await mc.build_market_context_block()
    assert "Trending: PEPE, WIF, JUP" in block
    assert "Our data (indexer/scanner): PEPE (0x1234…5678): our data: 1.2k holders" in block
    assert len(block.split()) <= 300  # token budget preserved


async def test_build_market_context_block_without_enrichment_stays_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """No own-data signals → block unchanged (no 'unverified' noise)."""
    monkeypatch.setattr(
        mc,
        "_cg",
        AsyncMock(
            side_effect=[
                {
                    "bitcoin": {"usd": 67234.5, "usd_24h_change": 1.8},
                    "ethereum": {"usd": 3521.25, "usd_24h_change": 2.3},
                },
                TRENDING_FIXTURE,
            ]
        ),
    )
    monkeypatch.setattr(
        mc, "_fng", AsyncMock(return_value={"data": [{"value": "68", "value_classification": "Greed"}]})
    )
    monkeypatch.setattr(mc, "_fetch_enrichment_lines", AsyncMock(return_value=[]))

    block = await mc.build_market_context_block()
    assert "Trending: PEPE, WIF, JUP" in block
    assert "Our data" not in block
