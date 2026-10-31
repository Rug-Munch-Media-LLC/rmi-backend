"""Tiered routing — consume a Lane C decision and turn it into send jobs.

The input is one routing decision per story, in the shape Lane C emits::

    {"slug": "...", "title": "...", "teaser": "...", "url": "...",
     "image_url": "...", "tags": ["..."],
     "lanes": ["main", "news"], "big_story": true, "score": 0.82}

Keys are read defensively (``lanes``/``route``/``decision``/``channels``,
``big_story``/``promote``/``high_importance``) because Lane C owns the shape
and this module must not force a rewrite of it. What this module *does* own:

* **promotion** — a ``big_story`` is always delivered to ``alerts`` *and*
  ``main``, whatever else the decision says;
* **caps** — ``RMI_TG_LANE_DAILY_CAP_<LANE>`` per UTC day, absent lanes fall
  back to ``DEFAULT_CAPS``, ``0``/negative means uncapped;
* **dedupe** — ``(day, lane, slug)`` via the ledger, so a story never repeats
  in a lane on the same day even when two plans run.

``site`` is addressable but has no Telegram surface, so it is skipped with a
reason instead of silently disappearing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.domains.telegram.editions.decisions import (
    PROMOTION_LANES,
    RouteDecision,
    parse_decision,
)
from app.domains.telegram.editions.lanes import LANES
from app.domains.telegram.editions.ledger import DeliveryLedger, dedupe_key

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DEFAULT_CAPS: dict[str, int] = {
    "main": 6,
    "alerts": 25,
    "news": 40,
    "alpha": 30,
    "whales": 20,
    "site": 0,
}

_LANE_ALIASES: dict[str, str] = {
    "main_channel": "main",
    "mainchannel": "main",
    "news_channel": "news",
    "alerts_channel": "alerts",
    "telegram": "main",
    "web": "site",
    "blog": "site",
}


@dataclass(frozen=True, slots=True)
class PlannedPost:
    """One story going to one lane."""

    decision: RouteDecision
    lane: str
    key: str


@dataclass(frozen=True, slots=True)
class Skipped:
    slug: str
    lane: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    posts: tuple[PlannedPost, ...] = ()
    skipped: tuple[Skipped, ...] = ()

    def for_lane(self, lane: str) -> tuple[PlannedPost, ...]:
        return tuple(post for post in self.posts if post.lane == lane)

    @property
    def lanes(self) -> tuple[str, ...]:
        return tuple(lane for lane in LANES if self.for_lane(lane))


def lane_cap(lane: str, env: Mapping[str, str] | None = None) -> int:
    """Per-UTC-day cap for *lane*: env override, else ``DEFAULT_CAPS``, else 0."""
    source = os.environ if env is None else env
    key = f"RMI_TG_LANE_DAILY_CAP_{lane.upper()}"
    raw = source.get(key)
    if raw is not None and str(raw).strip():
        try:
            return int(str(raw).strip())
        except ValueError:
            return DEFAULT_CAPS.get(lane, 0)
    return DEFAULT_CAPS.get(lane, 0)


def plan_delivery(
    decisions: Sequence[Mapping[str, Any] | RouteDecision],
    *,
    day: str,
    ledger: DeliveryLedger,
    env: Mapping[str, str] | None = None,
) -> DeliveryPlan:
    """Turn Lane C decisions into an ordered, capped, deduped send plan."""
    posts: list[PlannedPost] = []
    skipped: list[Skipped] = []
    admitted: dict[str, int] = {}

    for raw in decisions:
        try:
            decision = raw if isinstance(raw, RouteDecision) else parse_decision(raw)
        except ValueError as exc:
            skipped.append(Skipped(slug=str(raw.get("slug", "?")), lane=None, reason=f"unparseable:{exc}"))
            continue

        for lane in LANES:
            if lane not in decision.lanes:
                continue
            if lane == "site":
                skipped.append(Skipped(decision.slug, lane, "no_telegram_surface"))
                continue
            key = dedupe_key(day, lane, decision.slug, decision.url or decision.title)
            if ledger.already_sent(key):
                skipped.append(Skipped(decision.slug, lane, "duplicate"))
                continue
            cap = lane_cap(lane, env)
            used = ledger.lane_count(day, lane) + admitted.get(lane, 0)
            if cap > 0 and used >= cap:
                skipped.append(Skipped(decision.slug, lane, "cap_reached"))
                continue
            admitted[lane] = admitted.get(lane, 0) + 1
            posts.append(PlannedPost(decision=decision, lane=lane, key=key))

    return DeliveryPlan(posts=tuple(posts), skipped=tuple(skipped))


__all__ = [
    "DEFAULT_CAPS",
    "PROMOTION_LANES",
    "DeliveryPlan",
    "PlannedPost",
    "RouteDecision",
    "Skipped",
    "lane_cap",
    "parse_decision",
    "plan_delivery",
]
