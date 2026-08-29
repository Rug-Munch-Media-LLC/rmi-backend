"""Pure tests for the daily content/alerts pipeline — no network, no DB.

Monkeypatches the step functions so ``run_daily_pipeline`` is exercised
end-to-end deterministically: pipeline order, dry-run publish gating, a
failing step not aborting the rest, and the ``--step`` filter.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domains.content import daily_pipeline as dp
from app.domains.news_ingest.models import NewsArticle


def _article(title: str = "Headline") -> NewsArticle:
    return NewsArticle(
        title=title,
        body=f"{title} body",
        source_channel="cryptonews",
        source_url="https://t.me/cryptonews/1",
        category="crypto_news",
        posted_at=datetime(2026, 8, 13, 5, 0, tzinfo=UTC),
    )


def _ok_step_result(draft: str = "draft") -> dict[str, Any]:
    return {"draft": draft, "review": {"ok": True, "flags": []}, "published": None}


@pytest.fixture
def pipeline_fakes(monkeypatch) -> list[str]:
    """Install default passing step fakes; returns the call-order recorder."""
    calls: list[str] = []

    async def fake_news_step(**kwargs: Any) -> list[NewsArticle]:
        calls.append("news")
        return [_article()]

    async def fake_alerts_step(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append("alerts")
        return [{"channel": "site"}]

    async def fake_shill_step(articles: Any) -> list[Any]:
        calls.append("shill")
        return []

    async def fake_briefing_step(**kwargs: Any) -> dict[str, Any]:
        calls.append("briefing")
        return _ok_step_result()

    monkeypatch.setattr(dp, "run_news_step", fake_news_step)
    monkeypatch.setattr(dp, "run_alerts_step", fake_alerts_step)
    monkeypatch.setattr(dp, "run_shill_step", fake_shill_step)
    monkeypatch.setattr(dp, "run_briefing_step", fake_briefing_step)
    return calls


# ── Pipeline order + summary contract ───────────────────────────────────────


def test_steps_run_in_pipeline_order(pipeline_fakes: list[str]) -> None:
    calls = pipeline_fakes
    summary = asyncio.run(dp.run_daily_pipeline())

    assert calls == ["news", "alerts", "shill", "briefing"]
    assert all(summary[step]["ran"] for step in dp.STEPS)
    assert summary["news"]["count"] == 1
    assert summary["alerts"]["count"] == 1
    assert summary["briefing"]["count"] == 1
    assert summary["dry_run"] is True


def test_summary_contract_for_every_step(pipeline_fakes: list[str]) -> None:
    summary = asyncio.run(dp.run_daily_pipeline())

    assert set(dp.STEPS) <= set(summary)
    for step in dp.STEPS:
        assert set(summary[step]) == {"ran", "count", "error"}
        assert summary[step]["error"] is None


# ── dry-run gating ──────────────────────────────────────────────────────────


def test_dry_run_skips_publish(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_news_step(**kwargs: Any) -> list[Any]:
        return []

    async def fake_alerts_step(**kwargs: Any) -> list[Any]:
        return []

    async def fake_shill_step(articles: Any) -> list[Any]:
        return []

    async def fake_gather() -> dict[str, Any]:
        return {"market": {}}

    async def fake_draft(context: dict[str, Any]) -> str:
        return "🔍 RMI Morning Brief draft"

    def fake_review(draft: str, context: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "flags": []}

    async def fake_publish(draft: str) -> dict[str, Any]:
        calls.append("publish")
        return {"id": "post-1"}

    monkeypatch.setattr(dp, "run_news_step", fake_news_step)
    monkeypatch.setattr(dp, "run_alerts_step", fake_alerts_step)
    monkeypatch.setattr(dp, "run_shill_step", fake_shill_step)
    monkeypatch.setattr(dp, "gather_briefing_context", fake_gather)
    monkeypatch.setattr(dp, "draft_briefing", fake_draft)
    monkeypatch.setattr(dp, "review_briefing", fake_review)
    monkeypatch.setattr(dp, "publish_briefing", fake_publish)

    async def fake_publish_tg(draft: str, client: Any = None) -> dict[str, Any]:
        return {"skipped": 0, "sent": 0, "errors": []}

    # Must be patched: without it the live-run branch dials the real Telegram
    # headless client (network) and the test hangs until pytest-timeout fires.
    monkeypatch.setattr(dp, "publish_briefing_telegram", fake_publish_tg)

    # dry-run: assembled but never published
    summary = asyncio.run(dp.run_daily_pipeline(dry_run=True))
    assert calls == []
    assert summary["briefing"]["count"] == 1
    assert summary["briefing_draft"]["published"] is None

    # live: publish invoked and recorded
    summary = asyncio.run(dp.run_daily_pipeline(dry_run=False))
    assert calls == ["publish"]
    assert summary["briefing_draft"]["published"] == {"id": "post-1"}


# ── Fail-open ───────────────────────────────────────────────────────────────


def test_failing_step_does_not_abort_rest(monkeypatch) -> None:
    async def boom(**kwargs: Any) -> list[Any]:
        raise RuntimeError("news broken")

    async def ok_alerts_step(**kwargs: Any) -> list[dict[str, Any]]:
        return [{"channel": "site"}]

    async def ok_shill_step(articles: Any) -> list[Any]:
        return []

    async def ok_briefing_step(**kwargs: Any) -> dict[str, Any]:
        return _ok_step_result()

    monkeypatch.setattr(dp, "run_news_step", boom)
    monkeypatch.setattr(dp, "run_alerts_step", ok_alerts_step)
    monkeypatch.setattr(dp, "run_shill_step", ok_shill_step)
    monkeypatch.setattr(dp, "run_briefing_step", ok_briefing_step)

    summary = asyncio.run(dp.run_daily_pipeline())

    assert summary["news"]["error"] is not None
    assert "RuntimeError" in summary["news"]["error"]
    assert summary["news"]["count"] == 0
    # every other step still ran with its real count
    assert summary["alerts"]["ran"] is True
    assert summary["alerts"]["count"] == 1
    assert summary["shill"]["ran"] is True
    assert summary["shill"]["error"] is None
    assert summary["briefing"]["ran"] is True
    assert summary["briefing"]["count"] == 1


# ── Step filter ─────────────────────────────────────────────────────────────


def test_step_filter_runs_only_requested_step(pipeline_fakes: list[str]) -> None:
    calls = pipeline_fakes
    summary = asyncio.run(dp.run_daily_pipeline(steps={"briefing"}))

    assert calls == ["briefing"]
    assert summary["news"]["ran"] is False
    assert summary["alerts"]["ran"] is False
    assert summary["shill"]["ran"] is False
    assert summary["briefing"]["ran"] is True


def test_unknown_step_names_are_ignored(pipeline_fakes: list[str]) -> None:
    calls = pipeline_fakes
    summary = asyncio.run(dp.run_daily_pipeline(steps={"nope", "shill"}))

    assert calls == ["shill"]
    assert summary["news"]["ran"] is False
    assert summary["shill"]["ran"] is True


# ── CLI routing ─────────────────────────────────────────────────────────────


def test_cli_routes_dry_run_and_step_filter(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_run(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"news": {"ran": True, "count": 0, "error": None}}

    monkeypatch.setattr(dp, "run_daily_pipeline", fake_run)

    assert dp.main(["--run", "--step", "briefing"]) == 0
    assert captured["dry_run"] is True
    assert captured["steps"] == {"briefing"}

    captured.clear()
    assert dp.main(["--run", "--no-dry-run"]) == 0
    assert captured["dry_run"] is False
    assert captured["steps"] is None


def test_cli_exits_nonzero_when_a_step_errored(monkeypatch) -> None:
    async def failing(**kwargs: Any) -> dict[str, Any]:
        return {"news": {"ran": True, "count": 0, "error": "RuntimeError: boom"}}

    monkeypatch.setattr(dp, "run_daily_pipeline", failing)

    assert dp.main(["--run"]) == 1
