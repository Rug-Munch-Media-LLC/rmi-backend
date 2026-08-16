"""Happy-path tests for the Daily Market Brief job.

Covers ``app/domains/telegram/rugmunchbot/channels/daily_market_summary``:

  * ``build_messages`` — every data block (prices, condition, trending,
    movers, news, narratives, KOL signals) is serialized into the prompt.
  * ``post_daily_market_summary`` — mocked gather + mocked LLM → the cleaned
    report builds with all 6 canonical sections and ships to Telegram
    without crashing.

No live network, LLM, or DB — every external boundary is patched (mirrors
``tests/domains/test_daily_market_summary.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock, patch

from app.domains.telegram.rugmunchbot.channels import (
    daily_market_summary as dms,
    scheduler as channels_scheduler,
)

if TYPE_CHECKING:
    import pytest

# The 6 canonical always-on sections of the brief (NARRATIVE is conditional).
_SIX_SECTIONS = (
    "<b>MARKET OVERVIEW</b>",
    "<b>TRENDING MOVERS</b>",
    "<b>TOP NEWS</b>",
    "<b>SENTIMENT</b>",
    "<b>📣 X / KOL SIGNALS</b>",
    "<b>WHAT TO WATCH TODAY</b>",
)


def _context() -> dict[str, Any]:
    """A realistic gather result (every source present)."""
    return {
        "prices": {
            "btc_price": 67_231.15,
            "btc_change_24h": 2.1,
            "eth_price": 3412.5,
            "eth_change_24h": -0.4,
            "sol_price": 145.2,
            "sol_change_24h": 5.2,
        },
        "condition": {
            "verdict": "risk_on",
            "confidence": "medium",
            "reasons": ["Fear & Greed 62 (Greed) — risk-on"],
            "signals": {"fear_greed": {"value": 62, "classification": "Greed"}},
        },
        "trending": [
            {
                "symbol": "PEPE",
                "change_24h": 38.0,
                "volume_24h": 12_000_000,
                "market_cap": 4_000_000_000,
                "chain": "ethereum",
            },
        ],
        "movers": {
            "gainers": [{"symbol": "PEPE", "change_24h": 38.0, "volume_24h": 12_000_000, "market_cap": 4_000_000_000}],
            "losers": [{"symbol": "XYZ", "change_24h": -12.0, "volume_24h": 900_000, "market_cap": 50_000_000}],
        },
        "news": [{"content": "Ethereum ETF inflows hit record high as institutions rotate."}],
        "narratives": [
            {
                "query": "AI agents crypto narrative news today",
                "title": "AI agent tokens lead as crypto narrative shifts",
                "url": "https://example.com/ai-agents-narrative",
                "snippet": "AI agent tokens outperform as the sector narrative rotates.",
            },
        ],
        "kol_signals": [
            {
                "query": "PEPE crypto twitter trending today",
                "title": "KOLs pile into PEPE as volume spikes",
                "url": "https://example.com/pepe-shill",
                "snippet": "Top crypto influencers are shilling PEPE again.",
            },
        ],
        "sources": ["prices", "condition", "trending", "movers", "news", "narratives", "kol_signals"],
        "generated_at": "2026-08-16T12:00:00+00:00",
    }


def _ctx() -> tuple[Mock, AsyncMock]:
    """Minimal PTB callback context: ``ctx.bot`` is an AsyncMock bot."""
    bot = AsyncMock()
    ctx = Mock()
    ctx.bot = bot
    return ctx, bot


# ── build_messages: every block serializes into the prompt ────────────────


def test_build_messages_serializes_all_six_blocks() -> None:
    messages = dms.build_messages(_context())
    assert len(messages) == 2
    user = messages[1]["content"]
    for marker in (
        "MAJOR PRICES",
        "MARKET VERDICT",
        "FEAR & GREED",
        "TOP MOVERS",
        "TRENDING TOKENS",
        "TOP NEWS",
        "NARRATIVE SIGNALS",
        "X/KOL SIGNALS",
    ):
        assert marker in user, f"missing data block: {marker}"
    # The date line is deterministic from the mocked generated_at.
    assert "August 16, 2026" in user


# ── post_daily_market_summary: 6 sections + no crash ──────────────────────


def _six_section_raw() -> str:
    """A realistic LLM brief containing the 6 canonical sections."""
    return (
        "📊 <b>Daily Market Brief</b>\n\n"
        "<b>MARKET OVERVIEW</b>\nBTC $67,231 (+2.1%) · ETH $3,412 (-0.4%) · SOL $145 (+5.2%). "
        "Risk-on tape with breadth improving.\n\n"
        "<b>TRENDING MOVERS</b>\n• PEPE +38% — volume spike on the back of KOL chatter.\n\n"
        "<b>TOP NEWS</b>\n• Ethereum ETF inflows hit a record high (RAG news_v2).\n\n"
        "<b>SENTIMENT</b>\nFear & Greed 62/100 (Greed) — risk-on verdict.\n\n"
        "<b>📣 X / KOL SIGNALS</b>\n• X is talking about $PEPE because volume is spiking "
        '(<a href="https://example.com/pepe-shill">source</a>).\n\n'
        "<b>WHAT TO WATCH TODAY</b>\nWatch PEPE volume and the ETH ETF flow print."
    )


async def test_job_builds_report_with_six_sections_and_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _bot = _ctx()
    monkeypatch.setattr(dms, "gather_summary_data", AsyncMock(return_value=_context()))
    monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100main")
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(channels_scheduler, "_safe_send", send_mock)
    monkeypatch.setattr(dms, "DAILY_MARKET_SUMMARY_FEED_ENABLED", False)
    with patch("app.llm_config.chat_with_fallback", new=AsyncMock(return_value=_six_section_raw())) as llm_mock:
        result = await dms.post_daily_market_summary(ctx)

    # No crash: the job reports success and ships one Telegram message.
    assert result == 1
    llm_mock.assert_awaited_once()
    assert llm_mock.await_args.args[1] == "long"
    send_mock.assert_awaited_once()
    sent = send_mock.await_args.args[2]
    assert isinstance(sent, str)
    # All 6 canonical sections survived cleanup into the posted report.
    for section in _SIX_SECTIONS:
        assert section in sent, f"missing section: {section}"
    # The report is within the Telegram-safe budget.
    assert len(sent) <= dms.DAILY_MARKET_SUMMARY_MAX_CHARS + 1


async def test_job_full_pipeline_does_not_crash_with_all_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mocked data → full gather→write→clean→post pipeline never raises."""
    ctx, _bot = _ctx()
    monkeypatch.setattr(dms, "gather_summary_data", AsyncMock(return_value=_context()))
    monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100main")
    monkeypatch.setattr(channels_scheduler, "_safe_send", AsyncMock(return_value=True))
    monkeypatch.setattr(dms, "DAILY_MARKET_SUMMARY_FEED_ENABLED", False)
    with patch("app.llm_config.chat_with_fallback", new=AsyncMock(return_value=_six_section_raw())):
        result = await dms.post_daily_market_summary(ctx)
    assert result == 1


def test_system_prompt_defines_six_canonical_sections() -> None:
    order = [dms.SYSTEM_PROMPT.index(sec.replace("<b>", "").replace("</b>", "")) for sec in _SIX_SECTIONS]
    assert order == sorted(order)  # exact section order in the spec
