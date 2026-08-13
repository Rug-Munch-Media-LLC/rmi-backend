"""Pure tests for the three backend follow-up fixes.

- FIX 1: ``list_courses`` exposes ``image`` / ``image_alt`` on course dicts.
- FIX 2: ``prune_plan(stale_after_days=...)`` drops old STALE sources, keeps
  recent STALE, and default ``None`` keeps all STALE.
- FIX 3: ``llm_writer`` requests ``max_tokens=512`` by default (env
  ``RMI_BRIEFING_MAX_TOKENS`` overrides).

All tests are pure — no network, no DB, no app startup.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.databus.news_source_health import DEAD, STALE, VERIFIED, prune_plan
from app.domains.content.briefing import llm_writer
from app.routers.scam_school.content import COURSES
from app.routers.scam_school.routes.courses import list_courses

# ── FIX 1 — list_courses includes image / image_alt ─────────────────────────


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_list_courses_includes_image_when_present() -> None:
    """Course dicts returned by list_courses carry image + image_alt."""
    courses = _run(list_courses())["courses"]
    assert len(courses) == len(COURSES)
    content_by_id = {c["id"]: c for c in COURSES}
    for course in courses:
        assert "image" in course
        assert "image_alt" in course
        content = content_by_id[course["id"]]
        assert course["image"] == content.get("image")
        assert course["image_alt"] == content.get("image_alt")
        assert course["image"], f"course {course['id']} missing image"
        assert course["image_alt"], f"course {course['id']} missing image_alt"


# ── FIX 2 — prune_plan stale_after_days ─────────────────────────────────────


def make_source(source_id: str) -> dict:
    return {
        "id": source_id,
        "name": source_id.replace("-", " ").title(),
        "url": f"https://example.com/{source_id}/feed",
        "category": "crypto_news",
        "type": "rss",
        "priority": 3,
        "quality_tier": "established",
        "language": "en",
        "requires_api_key": False,
        "verified": True,
        "needs_verification": False,
    }


def make_status(source_id: str, status: str, *, last_good: str | None) -> dict:
    return {
        "id": source_id,
        "status": status,
        "type": "rss",
        "last_good": last_good,
        "item_count": 0 if status == DEAD else 5,
        "error": None,
    }


def stale_dataset() -> tuple[list[dict], dict]:
    """20 verified (floor-safe) + 1 old STALE + 1 recent STALE + 1 undated STALE."""
    now = datetime.now(UTC)
    old = (now - timedelta(days=90)).isoformat()
    recent = (now - timedelta(days=7)).isoformat()

    sources = [make_source(f"v-{i}") for i in range(20)]
    sources += [make_source("stale-old"), make_source("stale-recent"), make_source("stale-undated")]
    status: dict = {f"v-{i}": make_status(f"v-{i}", VERIFIED, last_good=recent) for i in range(20)}
    status["stale-old"] = make_status("stale-old", STALE, last_good=old)
    status["stale-recent"] = make_status("stale-recent", STALE, last_good=recent)
    status["stale-undated"] = make_status("stale-undated", STALE, last_good=None)
    return sources, status


def test_prune_plan_stale_after_days_drops_old_stale() -> None:
    sources, status = stale_dataset()
    plan = prune_plan(sources, status, stale_after_days=30)
    removed = {s["id"] for s in plan["remove"]}
    kept = {s["id"] for s in plan["keep"]}
    assert "stale-old" in removed, "old STALE should be dropped when stale_after_days set"
    assert "stale-old" not in kept


def test_prune_plan_stale_after_days_keeps_recent_stale() -> None:
    sources, status = stale_dataset()
    plan = prune_plan(sources, status, stale_after_days=30)
    removed = {s["id"] for s in plan["remove"]}
    kept = {s["id"] for s in plan["keep"]}
    assert "stale-recent" in kept, "recent STALE should be kept"
    assert "stale-recent" not in removed


def test_prune_plan_stale_after_days_keeps_undated_stale() -> None:
    """Fail-open: STALE with no last_good is never age-dropped."""
    sources, status = stale_dataset()
    plan = prune_plan(sources, status, stale_after_days=30)
    kept = {s["id"] for s in plan["keep"]}
    assert "stale-undated" in kept


def test_prune_plan_stale_after_days_default_none_keeps_all_stale() -> None:
    """Default (None) keeps current behavior — old STALE is never dropped."""
    sources, status = stale_dataset()
    plan = prune_plan(sources, status)
    removed = {s["id"] for s in plan["remove"]}
    kept = {s["id"] for s in plan["keep"]}
    assert "stale-old" in kept
    assert "stale-old" not in removed


# ── FIX 3 — llm_writer max_tokens ───────────────────────────────────────────

CONTEXT: dict[str, Any] = {"market": {}, "top_stories": [], "movers": [], "rug_watch": [], "whale_flows": []}

SKELETON: dict[str, Any] = {
    "title": "RMI Morning Brief — Thursday, August 13",
    "market_snapshot": "BTC N/A · ETH N/A · SOL N/A",
    "sections": dict.fromkeys(
        ("Market Snapshot", "Top Stories", "Movers & Trending", "Rug & Scam Watch", "On-Chain Notable", "The Take")
    ),
}


class CapturingCompletion:
    """Injectable ``completion`` that records kwargs and returns canned content."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or {"content": "prose"}
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.result


async def test_llm_writer_requests_max_tokens_512_by_default() -> None:
    completion = CapturingCompletion()
    assert await llm_writer(CONTEXT, SKELETON, completion=completion) == "prose"
    assert completion.calls[0]["max_tokens"] == 512


async def test_llm_writer_respects_max_tokens_env_override(monkeypatch: Any) -> None:
    monkeypatch.setenv("RMI_BRIEFING_MAX_TOKENS", "768")
    completion = CapturingCompletion()
    await llm_writer(CONTEXT, SKELETON, completion=completion)
    assert completion.calls[0]["max_tokens"] == 768


async def test_llm_writer_env_override_invalid_falls_back_to_default(monkeypatch: Any) -> None:
    monkeypatch.setenv("RMI_BRIEFING_MAX_TOKENS", "not-a-number")
    completion = CapturingCompletion()
    await llm_writer(CONTEXT, SKELETON, completion=completion)
    assert completion.calls[0]["max_tokens"] == 512
