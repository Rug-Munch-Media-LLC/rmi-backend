"""Pure tests for the RMI Morning Brief assembler — no network, no DB.

Covers ``assemble_market_snapshot``, ``draft_briefing`` (deterministic
fallback, no LLM) and ``review_briefing`` (hallucination / source gates).
"""

from __future__ import annotations

from app.domains.content.briefing import (
    SECTIONS,
    assemble_market_snapshot,
    draft_briefing,
    review_briefing,
)

MARKET = {
    "btc_price": 67231.15,
    "btc_change_24h": 2.1,
    "eth_price": 3412.5,
    "eth_change_24h": -0.4,
    "sol_price": 145.2,
    "sol_change_24h": 5.2,
}


# ── assemble_market_snapshot ────────────────────────────────────────────────


def test_market_snapshot_full_data() -> None:
    line = assemble_market_snapshot(MARKET)
    assert "BTC $67,231.15 (▲2.1%)" in line
    assert "ETH $3,412.50 (▼0.4%)" in line
    assert "SOL $145.20 (▲5.2%)" in line


def test_market_snapshot_missing_values_are_na() -> None:
    line = assemble_market_snapshot({})
    # BTC + ETH + SOL, each price and change → 6 N/A tokens
    assert line.count("N/A") >= 6
    assert "BTC N/A (N/A)" in line


def test_market_snapshot_zero_price_is_na_but_flat_change_is_real() -> None:
    line = assemble_market_snapshot({"btc_price": 0, "btc_change_24h": 0.0, "eth_price": None, "sol_price": "120"})
    assert "BTC N/A (0.0%)" in line
    assert "ETH N/A (N/A)" in line
    assert "SOL $120.00 (N/A)" in line


# ── draft_briefing — deterministic fallback (no LLM) ────────────────────────


def _full_context() -> dict:
    return {
        "market": dict(MARKET),
        "top_stories": [
            {
                "title": "Ethereum ETF inflows hit record",
                "detail": "Spot ETH ETFs saw record net inflows.",
                "source": "CoinDesk",
                "url": "https://example.com/eth-etf",
                "address": "",
            }
        ],
        "movers": [
            {
                "title": "PEPE",
                "detail": "0x6982508145454Ce325dDbE47a25d4ec3d2311933 up 38% / $12M vol",
                "source": "DexScreener",
                "url": "https://dexscreener.com/ethereum/pepe",
                "address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            }
        ],
        "rug_watch": [
            {
                "title": "MoonPump",
                "detail": "honeypot (sell disabled) at 0xdead0000000000000000000000000000000000beef",
                "source": "Scanner",
                "url": "https://example.com/moonpump",
                "address": "0xdead0000000000000000000000000000000000beef",
            }
        ],
        "whale_flows": [
            {
                "title": "Whale moved 2,500 BTC to Binance",
                "detail": "2,500 BTC (~$168M) sent to Binance hot wallet.",
                "source": "WhaleAlert",
                "url": "https://example.com/whale",
                "address": "",
            }
        ],
    }


async def test_draft_briefing_renders_all_six_sections() -> None:
    draft = await draft_briefing(_full_context())
    assert isinstance(draft, str)
    assert draft
    assert "RMI Morning Brief" in draft
    for section in SECTIONS:
        assert section in draft


async def test_draft_briefing_includes_addresses_where_present() -> None:
    draft = await draft_briefing(_full_context())
    assert "0x6982508145454Ce325dDbE47a25d4ec3d2311933" in draft  # mover address
    assert "0xdead0000000000000000000000000000000000beef" in draft  # rug address
    assert "PEPE" in draft


async def test_draft_briefing_empty_context_renders_na() -> None:
    draft = await draft_briefing({})
    assert "N/A" in draft
    for section in SECTIONS:
        assert section in draft


async def test_draft_briefing_deterministic_without_writer() -> None:
    ctx = _full_context()
    assert await draft_briefing(ctx) == await draft_briefing(ctx)


async def test_draft_briefing_injected_writer_is_used() -> None:
    async def fake_writer(context: dict, skeleton: dict) -> str:
        assert context["market"]["btc_price"] == 67231.15
        assert "Top Stories" in skeleton["sections"]
        return "WRITER OUTPUT — fresh prose"

    assert await draft_briefing(_full_context(), writer=fake_writer) == "WRITER OUTPUT — fresh prose"


async def test_draft_briefing_writer_failure_falls_back() -> None:
    async def broken_writer(context: dict, skeleton: dict) -> str:
        raise RuntimeError("llm down")

    draft = await draft_briefing({}, writer=broken_writer)
    assert "N/A" in draft  # deterministic fallback produced a real post


# ── review_briefing — pure fact-check ───────────────────────────────────────


def test_review_flags_fabricated_metric() -> None:
    context = {"market": dict(MARKET), "top_stories": [], "movers": [], "rug_watch": [], "whale_flows": []}
    draft = "📊 Market Snapshot\nBTC $99,999 (▲99.9%)\n\n🔥 Movers & Trending\n• FAKE — 0x1111111111111111111111111111111111111111 — up 1234%"
    review = review_briefing(draft, context)
    assert review["ok"] is False
    metrics = {f["metric"] for f in review["flags"] if f["type"] == "hallucination"}
    assert 99999.0 in metrics  # $99,999 not in context
    assert 99.9 in metrics  # 99.9% not in context
    assert 1234.0 in metrics  # 1234% not in context


async def test_review_passes_fully_grounded_draft() -> None:
    ctx = _full_context()
    draft = await draft_briefing(ctx)
    review = review_briefing(draft, ctx)
    assert review["ok"] is True, review["flags"]


async def test_review_flags_unsourced_claim_bullet() -> None:
    ctx = _full_context()
    # Same grounded numbers but the mover bullet loses its URL + address.
    grounded = review_briefing(await draft_briefing(ctx), ctx)
    assert grounded["ok"] is True

    ctx2 = {
        "market": dict(MARKET),
        "top_stories": [],
        "movers": [{"title": "PEPE", "detail": "up 38% / $12M vol", "source": "", "url": "", "address": ""}],
        "rug_watch": [],
        "whale_flows": [],
    }
    draft = await draft_briefing(ctx2)
    review = review_briefing(draft, ctx2)
    assert any(f["type"] == "unsourced" for f in review["flags"])


async def test_review_na_lines_are_not_flagged() -> None:
    draft = await draft_briefing({})  # all sections N/A — no numbers
    review = review_briefing(draft, {})
    assert review["ok"] is True, review["flags"]


# ── sanity: pytest-asyncio auto mode runs the async entrypoints ─────────────


async def test_draft_briefing_is_awaitable() -> None:
    draft = await draft_briefing({"market": dict(MARKET)})
    assert "BTC $67,231.15" in draft
    assert "The Take" in draft
