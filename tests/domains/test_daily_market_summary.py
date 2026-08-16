"""Unit tests for the Daily Market Summary job — no network, no LLM, no DB.

Covers:
  * ``cleanup_html`` — markdown-ish LLM output → safe Telegram HTML, escaping,
    char-budget enforcement (pure).
  * ``build_messages`` — message shape + deterministic date line (pure).
  * ``summary_run_time`` — 07:00 America/New_York daily trigger (pure).
  * ``post_daily_market_summary`` — gather → LLM → clean → post; fail-open on
    LLM failure; feed publish wiring; no-main-channel skip (LLM + send mocked).
  * registration — ``daily_market_summary`` registers on the JobQueue when
    enabled and the main chat id resolves.

No live network I/O — every external boundary is patched (gather, LLM, send,
feed publish), mirroring the rest of the bot test suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock, patch

from app.domains.telegram.rugmunchbot.channels import (
    daily_market_summary as dms,
    scheduler as channels_scheduler,
)

if TYPE_CHECKING:
    import pytest

# ── Shared fixtures / builders ─────────────────────────────────────────────


def _ctx() -> tuple[Mock, AsyncMock]:
    """Minimal PTB callback context: ``ctx.bot`` is an AsyncMock bot."""
    bot = AsyncMock()
    ctx = Mock()
    ctx.bot = bot
    return ctx, bot


def _context() -> dict[str, Any]:
    """A realistic gather result (every source present)."""
    return {
        "prices": {
            "btc_price": 67231.15,
            "btc_change_24h": 2.1,
            "eth_price": 3412.5,
            "eth_change_24h": -0.4,
            "sol_price": 145.2,
            "sol_change_24h": 5.2,
        },
        "condition": {
            "verdict": "risk_on",
            "confidence": "medium",
            "score": 0.5,
            "reasons": ["Fear & Greed 62 (Greed) — risk-on", "BTC 24h +2.1% — risk-on"],
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
        "sources": ["prices", "condition", "trending", "movers", "news"],
        "generated_at": "2026-08-16T12:00:00+00:00",
    }


async def _run_job(
    monkeypatch: pytest.MonkeyPatch,
    *,
    raw: str = "**report**",
    gather: dict[str, Any] | None = None,
    chat_id: str | None = "-100main",
    feed_enabled: bool = True,
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Run ``post_daily_market_summary`` with every external boundary mocked.

    Returns ``(send_mock, llm_mock, feed_mock)`` so tests can assert on the
    send call, the LLM tier, and the feed publish.
    """
    ctx, _bot = _ctx()
    if gather is not None:
        monkeypatch.setattr(dms, "gather_summary_data", AsyncMock(return_value=gather))
    monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: chat_id)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(channels_scheduler, "_safe_send", send_mock)
    monkeypatch.setattr(dms, "DAILY_MARKET_SUMMARY_FEED_ENABLED", feed_enabled)
    with (
        patch("app.llm_config.chat_with_fallback", new=AsyncMock(return_value=raw)) as llm_mock,
        patch.object(dms, "publish_to_feed", new=AsyncMock(return_value={"published": True})) as feed_mock,
    ):
        await dms.post_daily_market_summary(ctx)
    return send_mock, llm_mock, feed_mock  # type: ignore[return-value]


# ── cleanup_html (pure) ─────────────────────────────────────────────────────


def test_cleanup_converts_markdown_to_telegram_html() -> None:
    text = "# Market Overview\n**BTC** is up *big* today."
    out = dms.cleanup_html(text)
    assert "<b>Market Overview</b>" in out
    assert "<b>BTC</b>" in out
    assert "<i>big</i>" in out
    assert "**" not in out


def test_cleanup_escapes_plain_html_and_keeps_allowlisted_tags() -> None:
    text = "<b>BTC</b> < 100k & > 90k with <script>alert(1)</script>"
    out = dms.cleanup_html(text)
    assert "<b>BTC</b>" in out  # allowlisted tag survives
    assert "&lt;" in out
    assert "&amp;" in out
    assert "<script>" not in out


def test_cleanup_drops_code_fences() -> None:
    text = "Lead:\n```html\n<b>bold</b>\n```\nand `inline`."
    out = dms.cleanup_html(text)
    assert "```" not in out
    assert "<b>bold</b>" in out
    assert "<b>inline</b>" not in out  # inline code is plain text


def test_cleanup_enforces_char_budget() -> None:
    text = "Line one.\n\n" + "word " * 300
    out = dms.cleanup_html(text, max_chars=120)
    assert len(out) <= 121  # budget + ellipsis
    assert out.endswith("…")


def test_cleanup_empty_returns_empty() -> None:
    assert dms.cleanup_html("   \n ") == ""


# ── build_messages (pure) ───────────────────────────────────────────────────


def test_build_messages_shape_and_grounding() -> None:
    messages = dms.build_messages(_context())
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "Telegram HTML" in messages[0]["content"]
    assert "cliché" in messages[0]["content"].lower()  # bot-cliché ban is explicit
    user = messages[1]["content"]
    assert "BTC" in user
    assert "PEPE" in user
    assert "August 16, 2026" in user  # deterministic date from context


def test_build_messages_serializes_only_present_sources() -> None:
    messages = dms.build_messages({"prices": {"btc_price": 1.0}, "generated_at": "2026-08-16T00:00:00+00:00"})
    user = messages[1]["content"]
    assert "MAJOR PRICES" in user
    assert "TRENDING" not in user  # no trending block when absent


# ── summary_run_time (pure) ─────────────────────────────────────────────────


def test_summary_run_time_defaults_to_0700_et() -> None:
    t = dms.summary_run_time()
    assert t.hour == 7
    assert t.minute == 0
    assert str(t.tzinfo) == "America/New_York"


def test_summary_run_time_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dms, "DAILY_MARKET_SUMMARY_TIME", "08:30")
    monkeypatch.setattr(dms, "DAILY_MARKET_SUMMARY_TZ", "Europe/London")
    t = dms.summary_run_time()
    assert t.hour == 8
    assert t.minute == 30
    assert str(t.tzinfo) == "Europe/London"


# ── post_daily_market_summary (LLM + send mocked) ───────────────────────────


async def test_job_gathers_writes_cleans_and_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    send_mock, llm_mock, feed_mock = await _run_job(monkeypatch, raw="📊 <b>MARKET OVERVIEW</b>\nBTC $67,231 (+2.1%)")
    assert llm_mock.await_count == 1
    # The LLM call used the "long" tier (nemotron).
    assert llm_mock.await_args.args[1] == "long"
    send_mock.assert_awaited_once()
    sent = send_mock.await_args.args[2]
    assert "<b>MARKET OVERVIEW</b>" in sent
    # Feed publish called with the SAME content that went to Telegram.
    feed_mock.assert_awaited_once()
    assert feed_mock.await_args.args[0] == sent


async def test_job_skips_when_main_channel_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    send_mock, llm_mock, feed_mock = await _run_job(monkeypatch, chat_id=None)
    assert llm_mock.await_count == 0
    send_mock.assert_not_awaited()
    feed_mock.assert_not_awaited()


async def test_job_skips_when_llm_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    send_mock, llm_mock, feed_mock = await _run_job(monkeypatch, raw="")
    assert llm_mock.await_count == 1
    send_mock.assert_not_awaited()
    feed_mock.assert_not_awaited()


async def test_job_skips_when_no_data_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    send_mock, llm_mock, feed_mock = await _run_job(
        monkeypatch,
        gather={"prices": {}, "condition": {}, "trending": [], "movers": {}, "news": [], "sources": []},
    )
    assert llm_mock.await_count == 0
    send_mock.assert_not_awaited()
    feed_mock.assert_not_awaited()


async def test_job_does_not_publish_feed_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    send_mock, _llm_mock, feed_mock = await _run_job(monkeypatch, raw="report body", feed_enabled=False)
    assert send_mock.await_count == 1
    feed_mock.assert_not_awaited()


async def test_job_never_raises_on_llm_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, bot = _ctx()
    monkeypatch.setattr(dms, "gather_summary_data", AsyncMock(return_value=_context()))
    monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100main")
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(channels_scheduler, "_safe_send", send_mock)
    with patch("app.llm_config.chat_with_fallback", new=AsyncMock(side_effect=RuntimeError("provider down"))):
        result = await dms.post_daily_market_summary(ctx)
    assert result == 0
    send_mock.assert_not_awaited()
    assert bot.send_message.await_count == 0


# ── registration ────────────────────────────────────────────────────────────


class FakeJobQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def run_daily(self, callback: Any, time: Any, days: Any = None, name: Any = None, **kwargs: Any) -> None:
        self.jobs.append({"callback": callback, "time": time, "days": days, "name": name})

    def run_repeating(self, callback: Any, interval: Any, first: Any = None, name: Any = None, **kwargs: Any) -> None:
        self.jobs.append({"callback": callback, "interval": interval, "first": first, "name": name})

    def run_once(self, callback: Any, when: Any = None, name: Any = None, **kwargs: Any) -> None:
        self.jobs.append({"callback": callback, "when": when, "name": name})


def test_registers_daily_market_summary_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
    monkeypatch.setattr(channels_scheduler, "DAILY_MARKET_SUMMARY_ENABLED", True)
    monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100main" if channel == "main" else None)
    fake_jq = FakeJobQueue()
    app = SimpleNamespace(job_queue=fake_jq)
    channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
    names = {j["name"] for j in fake_jq.jobs}
    assert "daily_market_summary" in names
    job = next(j for j in fake_jq.jobs if j["name"] == "daily_market_summary")
    assert job["days"] == tuple(range(7))
    assert job["time"].hour == 7
    assert job["time"].minute == 0


def test_daily_market_summary_skips_when_main_channel_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
    monkeypatch.setattr(channels_scheduler, "DAILY_MARKET_SUMMARY_ENABLED", True)
    monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: None)
    fake_jq = FakeJobQueue()
    app = SimpleNamespace(job_queue=fake_jq)
    channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
    assert "daily_market_summary" not in {j["name"] for j in fake_jq.jobs}


def test_daily_market_summary_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
    monkeypatch.setattr(channels_scheduler, "DAILY_MARKET_SUMMARY_ENABLED", False)
    monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100main")
    fake_jq = FakeJobQueue()
    app = SimpleNamespace(job_queue=fake_jq)
    channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
    assert "daily_market_summary" not in {j["name"] for j in fake_jq.jobs}


# ── Daily Market Brief: narrative + X/KOL web-signal sections ───────────────


def _web_context() -> dict[str, Any]:
    """A gather result that also carries the two web-signal blocks."""
    ctx = _context()
    ctx["narratives"] = [
        {
            "query": "AI agents crypto narrative news today",
            "title": "AI agent tokens lead as crypto narrative shifts",
            "url": "https://example.com/ai-agents-narrative",
            "snippet": "AI agent tokens outperform as the sector narrative rotates.",
        },
    ]
    ctx["kol_signals"] = [
        {
            "query": "PEPE crypto twitter trending today",
            "title": "KOLs pile into PEPE as volume spikes",
            "url": "https://example.com/pepe-shill",
            "snippet": "Top crypto influencers are shilling PEPE again.",
        },
    ]
    ctx["sources"] = ctx["sources"] + ["narratives", "kol_signals"]
    return ctx


def test_build_messages_includes_narrative_and_kol_blocks() -> None:
    messages = dms.build_messages(_web_context())
    user = messages[1]["content"]
    assert "Daily Market Brief" in user  # renamed title
    assert "NARRATIVE SIGNALS (web search)" in user
    assert "X/KOL SIGNALS (web search)" in user
    assert "https://example.com/ai-agents-narrative" in user
    assert "https://example.com/pepe-shill" in user


def test_build_messages_omits_web_blocks_when_absent() -> None:
    messages = dms.build_messages(_context())
    user = messages[1]["content"]
    assert "NARRATIVE SIGNALS" not in user
    assert "X/KOL SIGNALS" not in user


def test_system_prompt_orders_brief_sections() -> None:
    sys_prompt = dms.SYSTEM_PROMPT
    assert "Daily Market Brief writer" in sys_prompt  # renamed identity
    order = [
        sys_prompt.index("MARKET OVERVIEW"),
        sys_prompt.index("🧭 NARRATIVE"),
        sys_prompt.index("TRENDING MOVERS"),
        sys_prompt.index("TOP NEWS"),
        sys_prompt.index("SENTIMENT"),
        sys_prompt.index("📣 X / KOL SIGNALS"),
        sys_prompt.index("WHAT TO WATCH TODAY"),
    ]
    assert order == sorted(order)  # exact section order
    assert "never invent a shill" in sys_prompt  # honest KOL rule


def test_kol_signal_queries_use_only_real_trending_tokens() -> None:
    queries = dms._kol_signal_queries(
        [
            {"symbol": "pepe"},
            {"symbol": " WIF "},
            {"symbol": ""},
            {"symbol": "BONK"},
        ]
    )
    assert queries == ["PEPE crypto twitter trending today", "WIF crypto twitter trending today"]
    # empty symbols are skipped, cap applies
    assert len(queries) <= dms.KOL_TOKEN_QUERIES_MAX


async def test_fetch_narratives_keeps_query_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    hits = [
        {"title": "AI agents lead", "url": "https://ex.com/1", "snippet": "snippet one"},
        {"title": "RWA narrative", "url": "https://ex.com/2", "snippet": ""},
    ]
    monkeypatch.setattr(
        "app.domains.telegram.rugmunchbot.tools.web.searxng_top_results",
        AsyncMock(return_value=hits),
    )
    out = await dms._fetch_narratives()
    assert out
    assert all(h.get("query") for h in out)  # every hit attributes its query
    assert any("AI agents" in h["query"] for h in out)


async def test_fetch_kol_signals_verifies_thin_snippets_via_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    hits = [
        {"title": "KOL shill", "url": "https://ex.com/pepe", "snippet": ""},  # thin → ladder fetch
        {"title": "Trending", "url": "https://ex.com/wif", "snippet": "rich snippet"},  # no fetch needed
    ]
    monkeypatch.setattr(
        "app.domains.telegram.rugmunchbot.tools.web.searxng_top_results",
        AsyncMock(return_value=hits),
    )
    monkeypatch.setattr(
        dms,
        "_kol_fetch_page",
        AsyncMock(return_value="<html><body>KOLs are shilling $PEPE hard on X</body></html>"),
    )
    out = await dms._fetch_kol_signals([{"symbol": "pepe"}, {"symbol": "wif"}])
    assert out
    thin = next(h for h in out if h["url"] == "https://ex.com/pepe")
    assert thin.get("page_preview")
    assert "$PEPE" in thin["page_preview"]
    rich = next(h for h in out if h["url"] == "https://ex.com/wif")
    assert "page_preview" not in rich  # no ladder call when the snippet is fine
    assert dms._kol_fetch_page.await_count == 1


async def test_dry_run_brief_produces_narrative_and_kol_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end dry run: mocked gather + mocked LLM → cleaned Brief with the
    new 🧭 NARRATIVE and 📣 X / KOL SIGNALS sections (source links survive)."""
    raw = (
        "📊 <b>Daily Market Brief</b>\n\n"
        "<b>MARKET OVERVIEW</b>\nBTC $67,231 (+2.1%) · ETH $3,412 (-0.4%) · SOL $145 (+5.2%)\n\n"
        "<b>🧭 NARRATIVE</b>\nAI agents are the day's dominant narrative — "
        '<a href="https://example.com/ai-agents-narrative">AI agent tokens lead</a>.\n\n'
        "<b>TRENDING MOVERS</b>\n• PEPE +38% — volume spike\n\n"
        "<b>TOP NEWS</b>\n• Ethereum ETF inflows hit record high\n\n"
        "<b>SENTIMENT</b>\nFear & Greed 62/100 (Greed)\n\n"
        "<b>📣 X / KOL SIGNALS</b>\n• X is talking about $PEPE because KOLs are piling back in "
        '(<a href="https://example.com/pepe-shill">source</a>)\n\n'
        "<b>WHAT TO WATCH TODAY</b>\nWatch PEPE volume\n"
    )
    send_mock, _llm_mock, _feed_mock = await _run_job(monkeypatch, raw=raw, gather=_web_context())
    sent = send_mock.await_args.args[2]
    assert "<b>🧭 NARRATIVE</b>" in sent
    assert "<b>📣 X / KOL SIGNALS</b>" in sent
    assert '<a href="https://example.com/pepe-shill">source</a>' in sent  # source link survives
    assert "$PEPE" in sent
