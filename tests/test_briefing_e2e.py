"""End-to-end tests for the RMI Morning Brief pipeline — no network, no DB.

Exercises ``run_briefing_step`` (and the Ghost/Telegram publishers) with
monkeypatched gather + publishers while the real assembler (``draft_briefing``
fallback, ``review_briefing``) runs. Asserts the §6 contract:

    gather → draft (LLM or fallback) → review → Ghost publish → Telegram post

and that a broken publisher never crashes the run (fail-open).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domains.content import briefing, daily_pipeline as dp

# Same grounded context shape as tests/test_briefing.py::_full_context —
# the deterministic fallback draft passes review against it.
MARKET = {
    "btc_price": 67231.15,
    "btc_change_24h": 2.1,
    "eth_price": 3412.5,
    "eth_change_24h": -0.4,
    "sol_price": 145.2,
    "sol_change_24h": 5.2,
}


def _context() -> dict[str, Any]:
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


# ── Full pipeline: gather → draft → review → Ghost → Telegram ───────────────


async def test_e2e_gather_draft_review_publish_and_telegram(monkeypatch) -> None:
    """Live mode: gather → draft (fallback) → review → both publishers."""
    calls: list[str] = []

    async def fake_gather() -> dict[str, Any]:
        calls.append("gather")
        return _context()

    async def fake_publish(draft: str, **kwargs: Any) -> dict[str, Any]:
        calls.append("publish")
        assert draft  # the drafted body flows to Ghost
        return {
            "id": "ghost-1",
            "title": "RMI Morning Brief — Thursday, August 13, 2026",
            "url": "https://blog.rugmunch.io/briefing/",
        }

    async def fake_telegram(draft: str, **kwargs: Any) -> dict[str, Any]:
        calls.append("telegram")
        assert draft
        return {"sent": 1, "skipped": 0, "errors": []}

    monkeypatch.setattr(dp, "gather_briefing_context", fake_gather)
    monkeypatch.setattr(dp, "publish_briefing", fake_publish)
    monkeypatch.setattr(dp, "publish_briefing_telegram", fake_telegram)
    # draft_briefing + review_briefing stay REAL (deterministic fallback).

    result = await dp.run_briefing_step(dry_run=False, now=datetime(2026, 8, 13, 5, 30, tzinfo=UTC))

    assert calls == ["gather", "publish", "telegram"]
    # draft produced (fallback render) + review ran and passed
    assert result["draft"]
    assert "RMI Morning Brief" in result["draft"]
    assert result["review"]["ok"] is True, result["review"]["flags"]
    assert result["published"] == {
        "id": "ghost-1",
        "title": "RMI Morning Brief — Thursday, August 13, 2026",
        "url": "https://blog.rugmunch.io/briefing/",
    }
    assert result["telegram"]["sent"] == 1
    assert result.get("publish_error") is None
    assert result.get("telegram_error") is None


async def test_e2e_llm_writer_prose_is_published(monkeypatch) -> None:
    """The writer output (LLM-style prose) is what gets published."""
    published: dict[str, Any] = {}

    async def fake_gather() -> dict[str, Any]:
        return _context()

    async def fake_draft(context: dict[str, Any], **kwargs: Any) -> str:
        # stand-in for llm_writer: fresh prose, not the template
        return "🔍 RMI Morning Brief — Thursday, August 13\n📊 BTC $67,231.15 (▲2.1%)"

    async def fake_publish(draft: str, **kwargs: Any) -> dict[str, Any]:
        published["draft"] = draft
        return {"id": "ghost-1", "url": "https://blog.rugmunch.io/briefing/"}

    async def fake_telegram(draft: str, **kwargs: Any) -> dict[str, Any]:
        return {"sent": 1, "skipped": 0, "errors": []}

    monkeypatch.setattr(dp, "gather_briefing_context", fake_gather)
    monkeypatch.setattr(dp, "draft_briefing", fake_draft)
    monkeypatch.setattr(dp, "publish_briefing", fake_publish)
    monkeypatch.setattr(dp, "publish_briefing_telegram", fake_telegram)

    result = await dp.run_briefing_step(dry_run=False)

    assert result["draft"].startswith("🔍 RMI Morning Brief")
    assert published["draft"] == result["draft"]
    assert result["review"]["ok"] is True  # numbers grounded in context


# ── Fail-open: a broken publisher never crashes the run ─────────────────────


async def test_e2e_broken_publishers_do_not_crash(monkeypatch) -> None:
    """Both publishers raising → run still returns draft + review + errors."""

    async def fake_gather() -> dict[str, Any]:
        return {"market": {}}  # empty → N/A draft, review passes

    async def broken_publish(draft: str, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("ghost down")

    async def broken_telegram(draft: str, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("telegram down")

    monkeypatch.setattr(dp, "gather_briefing_context", fake_gather)
    monkeypatch.setattr(dp, "publish_briefing", broken_publish)
    monkeypatch.setattr(dp, "publish_briefing_telegram", broken_telegram)

    result = await dp.run_briefing_step(dry_run=False)

    assert result["draft"]
    assert "N/A" in result["draft"]
    assert result["review"]["ok"] is True
    assert result["published"] is None
    assert result["telegram"] is None
    assert "RuntimeError" in result.get("publish_error", "")
    assert "RuntimeError" in result.get("telegram_error", "")


async def test_e2e_dry_run_skips_both_publishers(monkeypatch) -> None:
    """dry_run (owner veto) assembles but calls no publisher."""
    calls: list[str] = []

    async def fake_gather() -> dict[str, Any]:
        calls.append("gather")
        return _context()

    async def fake_publish(draft: str, **kwargs: Any) -> dict[str, Any]:
        calls.append("publish")
        return {"id": "ghost-1"}

    async def fake_telegram(draft: str, **kwargs: Any) -> dict[str, Any]:
        calls.append("telegram")
        return {"sent": 1}

    monkeypatch.setattr(dp, "gather_briefing_context", fake_gather)
    monkeypatch.setattr(dp, "publish_briefing", fake_publish)
    monkeypatch.setattr(dp, "publish_briefing_telegram", fake_telegram)

    result = await dp.run_briefing_step(dry_run=True)

    assert calls == ["gather"]
    assert result["draft"]
    assert "RMI Morning Brief" in result["draft"]
    assert result["review"]["ok"] is True
    assert result["published"] is None
    assert result["telegram"] is None


# ── Ghost publish contract (post_type/title/tags) ───────────────────────────


async def test_publish_briefing_calls_ghost_with_type_and_title(monkeypatch) -> None:
    """``publish_briefing`` → ``create_post`` with post_type="briefing" + title."""
    captured: dict[str, Any] = {}

    async def fake_create_post(
        content: str,
        post_type: str,
        title: str,
        tags: list[str] | None = None,
        status: str = "published",
        visibility: str = "public",
    ) -> dict[str, Any]:
        captured.update(content=content, post_type=post_type, title=title, tags=tags)
        return {
            "id": "ghost-42",
            "title": title,
            "url": "https://blog.rugmunch.io/briefing/rmi-morning-brief/",
            "status": "published",
        }

    monkeypatch.setattr("app.content_platform.create_post", fake_create_post)

    result = await briefing.publish_briefing(
        "🔍 RMI Morning Brief body",
        now=datetime(2026, 8, 13, 5, 30, tzinfo=UTC),
    )

    assert captured["post_type"] == "briefing"
    assert captured["title"] == "RMI Morning Brief — Thursday, August 13, 2026"
    assert captured["content"] == "🔍 RMI Morning Brief body"
    assert captured["tags"] == ["briefing"]
    assert result["id"] == "ghost-42"
    assert result["url"].startswith("https://blog.rugmunch.io/")


# ── Telegram publish (optional step, env-driven channel ids) ────────────────


async def test_telegram_publish_skipped_without_channel() -> None:
    """No configured channel id → skipped + logged, never raises."""
    result = await briefing.publish_briefing_telegram("draft body", channel_ids={})
    assert result == {"sent": 0, "skipped": 1, "errors": [], "reason": "no_chat_id"}


async def test_telegram_publish_sends_compact_to_configured_channel() -> None:
    """Configured channel → compact briefing sent to main_channel."""
    sent: list[tuple[Any, str]] = []

    class FakeClient:
        async def send_message(self, chat_id: Any, message: str, **kwargs: Any) -> bool:
            sent.append((chat_id, message))
            return True

    long_draft = "\n\n".join([f"Section {i} " + "filler " * 400 for i in range(12)])
    result = await briefing.publish_briefing_telegram(
        long_draft,
        client=FakeClient(),
        channel_ids={"main_channel": -100123, "rmicryptonews": -100456},
    )

    assert result["sent"] == 1
    assert len(sent) == 1
    assert sent[0][0] == -100123  # main_channel preferred over rmicryptonews
    assert len(sent[0][1]) <= briefing.BRIEFING_TELEGRAM_LIMIT


async def test_telegram_publish_fail_open_on_send_error() -> None:
    """A broken transport returns an error entry, never raises."""

    class BrokenClient:
        async def send_message(self, chat_id: Any, message: str, **kwargs: Any) -> bool:
            raise ConnectionError("transport down")

    result = await briefing.publish_briefing_telegram(
        "draft",
        client=BrokenClient(),
        channel_ids={"main_channel": -100123},
    )
    assert result["sent"] == 0
    assert len(result["errors"]) == 1
    assert "transport down" in result["errors"][0]
