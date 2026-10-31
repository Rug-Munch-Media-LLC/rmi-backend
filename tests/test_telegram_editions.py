"""Tests for the Telegram editions lane: rendering, promotion, caps, delivery.

No network, no gopass, no clock dependence: the sender is a fake, the ledger is
in-memory, and every env-driven decision is passed in explicitly.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.telegram.editions import (
    CAPTION_LIMIT,
    DEFAULT_CAPS,
    DeliveryConfig,
    InMemoryLedger,
    SentMessage,
    dedupe_key,
    deliver,
    edition_title,
    get_edition,
    lane_live,
    plan_delivery,
    publish_edition,
    render_edition,
    story_payload,
)

DAY = "2026-10-29"
SITE = "https://rugmunch.io"
CHAT_IDS = {"main": "-1001", "alerts": "-1002", "news": "-1003", "alpha": "-1004"}


def _env(**overrides: str) -> dict[str, str]:
    """Deterministic channel env: every lane has an id and every gate is live."""
    env = {f"RMI_{lane.upper()}_CHANNEL_ID": cid for lane, cid in CHAT_IDS.items()}
    env |= {f"RMI_CHANNEL_DRY_RUN_{lane.upper()}": "0" for lane in CHAT_IDS}
    env |= overrides
    return env


def _decision(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "slug": "story",
        "title": "A story",
        "teaser": "Teaser text.",
        "url": f"{SITE}/story/s",
    }
    base |= overrides
    return base


class FakeSender:
    """Records sends; raises a mapped error for a chat id, optionally N times."""

    def __init__(self, errors: dict[Any, BaseException] | None = None, fail_first: int = 0) -> None:
        self.errors = dict(errors or {})
        self.fail_first = fail_first
        self.attempts: dict[Any, int] = {}
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: Any, payload: Any, *, lane: str) -> SentMessage:
        count = self.attempts[chat_id] = self.attempts.get(chat_id, 0) + 1
        self.sent.append((lane, payload.text))
        error = self.errors.get(chat_id)
        if error is not None and (self.fail_first == 0 or count <= self.fail_first):
            raise error
        return SentMessage(ok=True, message_id=count)


def _render(post: Any) -> Any:
    return story_payload(lead=post.decision.raw, site_url=SITE, lane=post.lane)


def _sleeper() -> tuple[list[float], Any]:
    delays: list[float] = []

    async def _sleep(seconds: float) -> None:
        delays.append(seconds)

    return delays, _sleep


async def _run(
    decisions: list[dict[str, Any]],
    *,
    ledger: InMemoryLedger | None = None,
    env: dict[str, str] | None = None,
    sender: FakeSender | None = None,
    sleep: Any = None,
) -> tuple[Any, Any, InMemoryLedger]:
    ledger = ledger or InMemoryLedger()
    source = _env() if env is None else env
    plan = plan_delivery(decisions, day=DAY, ledger=ledger, env=source)
    report = await deliver(
        plan,
        sender=sender or FakeSender(),
        ledger=ledger,
        day=DAY,
        render=_render,
        env=source,
        config=DeliveryConfig(lane_min_interval_s=0.0, jitter=False),
        use_gopass=False,
        sleep=sleep,
    )
    return plan, report, ledger


# ── payload budget and escaping ───────────────────────────────────────────


def test_payload_fits_caption_budget() -> None:
    lead = {
        "slug": "s1",
        "title": "Liquidity Drain",
        "url": f"{SITE}/story/s1",
        "tags": ["rug", "liquidity"],
        "teaser": "Liquidity was pulled in one block. " * 300,
    }
    payload = render_edition(get_edition("morning"), lead, env={})
    assert payload.chars <= CAPTION_LIMIT
    assert "The Morning Rug" in payload.text
    assert f"{SITE}/story/s1" in payload.text
    assert "#rug" in payload.text
    assert "#liquidity" in payload.text


def test_html_escaped_in_title_teaser_and_link() -> None:
    lead = {
        "slug": "s2",
        "title": "Rug & <b>Roll</b>",
        "teaser": "A <script>alert(1)</script> & more.",
        "url": f"{SITE}/story/s2?a=1&b=2",
    }
    story = story_payload(lead=lead, site_url=SITE, lane="news").text
    assert "<b>Roll" not in story
    assert "&lt;b&gt;Roll&lt;/b&gt;" in story
    edition = render_edition(get_edition("evening"), lead, env={}).text
    assert "<script>" not in edition
    assert "&lt;script&gt;" in edition
    assert "&amp; more." in edition
    assert "?a=1&amp;b=2" in edition


def test_teaser_cut_at_sentence_boundary() -> None:
    teaser = " ".join(f"Sentence {n} explains the drain in detail." for n in range(200))
    lead = {"slug": "s3", "title": "T", "url": f"{SITE}/story/s3", "teaser": teaser}
    text = render_edition(get_edition("midday"), lead, env={}).text
    assert len(text) <= CAPTION_LIMIT
    part = text.split("\n\n")[1]
    assert teaser.startswith(part)
    assert part.endswith(".")
    assert "…" not in part


# ── exact brand titles ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "title"),
    [
        ("morning", "Rug Munch Intelligence — The Morning Rug"),
        ("midday", "Rug Munch Intelligence — Midday Market Report"),
        ("evening", "Rug Munch Intelligence — Evening Market Closing Report"),
    ],
)
def test_exact_edition_titles(key: str, title: str) -> None:
    assert edition_title(get_edition(key), env={}) == title
    assert title in render_edition(get_edition(key), {"slug": "s", "title": "x"}, env={}).text


# ── promotion ─────────────────────────────────────────────────────────────


def test_big_story_adds_alerts_and_main_once() -> None:
    plan = plan_delivery(
        [_decision(lanes=["news"], big_story=True)], day=DAY, ledger=InMemoryLedger(), env=_env()
    )
    lanes = [post.lane for post in plan.posts]
    assert sorted(lanes) == ["alerts", "main", "news"]
    assert len(lanes) == len(set(lanes))


def test_big_story_already_declared_is_not_duplicated() -> None:
    plan = plan_delivery(
        [_decision(lanes=["main", "alerts"], big_story=True)],
        day=DAY,
        ledger=InMemoryLedger(),
        env=_env(),
    )
    assert [post.lane for post in plan.posts] == ["main", "alerts"]


# ── caps ──────────────────────────────────────────────────────────────────


def test_default_lane_cap_skips_over_cap() -> None:
    ledger = InMemoryLedger()
    for n in range(3):
        ledger.record(dedupe_key(DAY, "main", f"old{n}"), day=DAY, lane="main", slug=f"old{n}")
    decisions = [_decision(slug=f"s{n}", lanes=["main"]) for n in range(5)]
    plan = plan_delivery(decisions, day=DAY, ledger=ledger, env=_env())
    assert len(plan.for_lane("main")) == DEFAULT_CAPS["main"] - 3
    assert [skip.reason for skip in plan.skipped] == ["cap_reached"] * 2


@pytest.mark.parametrize(("cap", "expected"), [("2", 2), ("0", 5)])
def test_env_cap_override(cap: str, expected: int) -> None:
    decisions = [_decision(slug=f"s{n}", lanes=["main"]) for n in range(5)]
    plan = plan_delivery(
        decisions, day=DAY, ledger=InMemoryLedger(), env=_env(RMI_TG_LANE_DAILY_CAP_MAIN=cap)
    )
    assert len(plan.for_lane("main")) == expected


# ── dedupe and ledger idempotency ─────────────────────────────────────────


async def test_same_day_lane_slug_delivered_once() -> None:
    ledger = InMemoryLedger()
    decision = _decision(slug="dup", lanes=["main"])
    _, first, _ = await _run([decision], ledger=ledger)
    assert first.sent == 1
    plan, second, _ = await _run([decision], ledger=ledger)
    assert plan.posts == ()
    assert second.sent == 0
    assert plan.skipped[0].reason == "duplicate"


async def test_same_slug_in_two_lanes_once_each() -> None:
    _, report, ledger = await _run([_decision(slug="dup", lanes=["main", "news"])])
    assert report.sent == 2
    assert {row.key for row in ledger.rows(DAY)} == {
        dedupe_key(DAY, "main", "dup"),
        dedupe_key(DAY, "news", "dup"),
    }


async def test_two_plans_are_idempotent() -> None:
    ledger = InMemoryLedger()
    decisions = [_decision(slug="a", lanes=["main", "news"]), _decision(slug="b", lanes=["main"])]
    _, first, _ = await _run(decisions, ledger=ledger)
    assert first.sent == 3
    plan, second, _ = await _run(decisions, ledger=ledger)
    assert plan.posts == ()
    assert second.sent == 0
    assert {skip.reason for skip in plan.skipped} == {"duplicate"}


# ── dry lanes and the site lane ───────────────────────────────────────────


async def test_dry_lane_skips_without_error() -> None:
    sender = FakeSender()
    plan, report, ledger = await _run(
        [_decision(slug="d", lanes=["main"])], env=_env(RMI_CHANNEL_DRY_RUN_MAIN="1"), sender=sender
    )
    assert plan.posts
    assert report.sent == report.failed == 0
    assert report.skipped == 1
    assert sender.sent == []
    assert [row["reason"] for row in report.skip_reasons] == ["lane_dry"]
    assert ledger.rows() == ()


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"RMI_CHANNEL_DRY_RUN_SITE": "0"},
        {"RMI_CHANNEL_DRY_RUN": "1"},
        {"RMI_CHANNEL_DRY_RUN": "0"},
    ],
)
def test_site_always_dry_and_skipped(env: dict[str, str]) -> None:
    assert lane_live("site", env) is False
    plan = plan_delivery(
        [_decision(slug="s", lanes=["site"])], day=DAY, ledger=InMemoryLedger(), env=_env(**env)
    )
    assert plan.posts == ()
    assert [skip.reason for skip in plan.skipped] == ["no_telegram_surface"]


# ── retry and failure isolation ───────────────────────────────────────────


async def test_transient_error_retried_then_succeeds() -> None:
    delays, sleep = _sleeper()
    sender = FakeSender(errors={-1001: TimeoutError("timeout")}, fail_first=1)
    _, report, _ = await _run([_decision(slug="r", lanes=["main"])], sender=sender, sleep=sleep)
    assert report.sent == 1
    assert report.failed == 0
    assert report.results[0].attempts == 2
    assert delays


async def test_permanent_error_attempted_once() -> None:
    delays, sleep = _sleeper()
    sender = FakeSender(errors={-1001: ValueError("CHAT_WRITE_FORBIDDEN")})
    _, report, ledger = await _run(
        [_decision(slug="p", lanes=["main"])], sender=sender, sleep=sleep
    )
    assert report.failed == 1
    assert report.sent == 0
    assert report.results[0].attempts == 1
    assert delays == []
    assert ledger.rows() == ()


async def test_lane_failure_does_not_stop_other_lane() -> None:
    sender = FakeSender(errors={-1001: ValueError("CHAT_WRITE_FORBIDDEN")})
    _, report, _ = await _run([_decision(slug="iso", lanes=["main", "news"])], sender=sender)
    assert report.failed == 1
    assert report.sent == 1
    assert [lane for lane, _ in sender.sent] == ["main", "news"]


# ── publish_edition ───────────────────────────────────────────────────────


async def test_publish_edition_is_idempotent() -> None:
    ledger = InMemoryLedger()
    sender = FakeSender()
    lead = {"slug": "ed-lead", "teaser": "Teaser.", "url": f"{SITE}/story/ed-lead"}
    kwargs = {"sender": sender, "ledger": ledger, "env": _env(), "date": DAY, "use_gopass": False}
    first = await publish_edition("morning", lead, **kwargs)
    assert first.sent == 1
    assert "The Morning Rug" in sender.sent[0][1]
    second = await publish_edition("morning", lead, **kwargs)
    assert second.sent == 0
