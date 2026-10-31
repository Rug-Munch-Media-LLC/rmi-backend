"""Read one Lane C routing decision, defensively.

Lane C owns the decision shape; this module must not force a rewrite of it.
So the reader accepts the names a router plausibly emits and normalises them:

    lanes     lanes | route | decision | channels | targets | publish_to
    big story big_story | promote | high_importance | is_big_story
    teaser    teaser | dek | summary | description
    link      url | canonical_url | link
    image     image_url | image | og_image
    score     score | priority | importance

A ``big_story`` is always routed to ``alerts`` *and* ``main`` on top of
whatever else the decision asked for — that promotion rule is this lane's, and
it is applied here so caps and dedupe see the final lane set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.domains.telegram.editions.lanes import LANES

if TYPE_CHECKING:
    from collections.abc import Mapping

PROMOTION_LANES: tuple[str, ...] = ("alerts", "main")

_LANE_ALIASES: dict[str, str] = {
    "main_channel": "main",
    "mainchannel": "main",
    "news_channel": "news",
    "alerts_channel": "alerts",
    "telegram": "main",
    "web": "site",
    "blog": "site",
}

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """One story plus the lanes it belongs in (post-promotion)."""

    slug: str
    title: str
    teaser: str = ""
    url: str = ""
    image_url: str | None = None
    tags: tuple[str, ...] = ()
    lanes: tuple[str, ...] = ("main",)
    big_story: bool = False
    score: float = 0.0
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "teaser": self.teaser,
            "url": self.url,
            "image_url": self.image_url,
            "tags": list(self.tags),
            "lanes": list(self.lanes),
            "big_story": self.big_story,
            "score": self.score,
        }


def _first(raw: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", (), []):
            return value
    return default


def _normalise_lane(value: object) -> str | None:
    lane = str(value).strip().lower()
    lane = _LANE_ALIASES.get(lane, lane)
    return lane if lane in LANES else None


def _lane_list(raw: Mapping[str, Any]) -> tuple[str, ...]:
    declared = _first(raw, "lanes", "route", "decision", "channels", "targets", "publish_to")
    if isinstance(declared, str):
        declared = declared.replace("|", ",").split(",")
    if not declared:
        return ("main",)
    return tuple(lane for lane in (_normalise_lane(item) for item in declared) if lane)


def _is_big_story(raw: Mapping[str, Any]) -> bool:
    flag = _first(raw, "big_story", "promote", "high_importance", "is_big_story", default=False)
    if isinstance(flag, str):
        return flag.strip().lower() in _TRUE_STRINGS
    return bool(flag)


def _score(raw: Mapping[str, Any]) -> float:
    try:
        return float(_first(raw, "score", "priority", "importance", default=0.0))
    except (TypeError, ValueError):
        return 0.0


def _tags(raw: Mapping[str, Any]) -> tuple[str, ...]:
    tags = _first(raw, "tags", "keywords", default=())
    if isinstance(tags, str):
        tags = tags.replace("|", ",").split(",")
    return tuple(str(tag) for tag in tags)


def parse_decision(raw: Mapping[str, Any]) -> RouteDecision:
    """Read one Lane C decision; raises ``ValueError`` when it is unusable."""
    slug = str(_first(raw, "slug", "story_slug", "id", default="") or "").strip()
    title = str(_first(raw, "title", "headline", default="") or "").strip()
    if not slug and not title:
        raise ValueError("decision has neither slug nor title")
    lanes = _lane_list(raw)
    big_story = _is_big_story(raw)
    if big_story:
        lanes = tuple(dict.fromkeys((*lanes, *PROMOTION_LANES)))
    return RouteDecision(
        slug=slug,
        title=title,
        teaser=str(_first(raw, "teaser", "dek", "summary", "description", default="") or ""),
        url=str(_first(raw, "url", "canonical_url", "link", default="") or ""),
        image_url=_first(raw, "image_url", "image", "og_image"),
        tags=_tags(raw),
        lanes=lanes,
        big_story=big_story,
        score=_score(raw),
        raw=raw,
    )


__all__ = ["PROMOTION_LANES", "RouteDecision", "parse_decision"]
