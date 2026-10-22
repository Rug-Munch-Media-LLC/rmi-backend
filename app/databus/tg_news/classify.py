"""Deterministic classification + trust scoring for Telegram news posts.

No model calls: the score is a fixed, auditable rubric so Rugbrain can weight
sources later without re-deriving anything.

Post rubric (start 55):
    +15 links to a non-Telegram (primary) source      -15 forwarded post
    -10 no links at all                               -10 body shorter than 80 chars
    -5  clickbait marker ("JUST IN", "BREAKING", …)   -5  aggregator channel

Bands: ``high`` >= 70, ``medium`` >= 45, else ``low``.

``KIND_WEIGHTS`` is the multiplier Rugbrain applies per channel role
(``primary`` 1.0, ``security`` 0.9, ``aggregator`` 0.35, unknown 0.5); it is emitted both per
post (``kind_weight``) and on the channel profile (``trust_weight``).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_BAIT_MARKERS = ("just in", "breaking", "urgent", "🚨", "⚠️", "🔥")
_WS_RE = re.compile(r"\s+")
KIND_AGGREGATOR = "aggregator"
KIND_PRIMARY = "primary"
KIND_SECURITY = "security"
KIND_SHILL = "shill"
KIND_UNKNOWN = "unknown"
#: Weight persisted with each source; ``shill`` is 0.0 by design (monitored only).
KIND_WEIGHTS = {
    KIND_PRIMARY: 1.0,
    # Security/scam primaries: own reporting, but single-source and unverified
    # until a second desk or the chain confirms (matches news_fallback's 0.90).
    KIND_SECURITY: 0.9,
    KIND_AGGREGATOR: 0.35,
    KIND_SHILL: 0.0,
    KIND_UNKNOWN: 0.5,
}


def trust_band(score: int) -> str:
    if score >= 70:
        return "high"
    return "medium" if score >= 45 else "low"


def score_post(post: dict[str, Any], channel_kind: str = "unknown") -> dict[str, Any]:
    """Attach link/forward flags and a deterministic trust score to one post."""
    text = str(post.get("text") or "")
    links = [str(link) for link in post.get("links") or []]
    external = [link for link in links if link.startswith("http") and "t.me/" not in link]
    forward = post.get("forward_from")
    score = 55
    score += 15 if external else -10
    score -= 15 if forward else 0
    score -= 10 if len(text) < 80 else 0
    lowered = _WS_RE.sub(" ", text.lower())
    score -= 5 if any(marker in lowered for marker in _BAIT_MARKERS) else 0
    score -= 5 if channel_kind == KIND_AGGREGATOR else 0
    score = max(0, min(100, score))
    return {
        **post,
        "has_link": bool(external),
        "link_count": len(links),
        "is_forward": bool(forward),
        "channel_kind": channel_kind,
        "kind_weight": KIND_WEIGHTS.get(channel_kind, 0.5),
        "trust_score": score,
        "trust_band": trust_band(score),
    }


def _hours_span(posts: list[dict[str, Any]]) -> float:
    stamps = []
    for post in posts:
        try:
            stamps.append(datetime.fromisoformat(str(post.get("timestamp")).replace("Z", "+00:00")))
        except (TypeError, ValueError):
            continue
    if len(stamps) < 2:
        return 1.0
    return max((max(stamps) - min(stamps)).total_seconds() / 3600.0, 1.0)


def channel_profile(posts: list[dict[str, Any]], kind_hint: str | None = None) -> dict[str, Any]:
    """Channel-level metrics: kind, forward/link shares, cadence, trust."""
    total = len(posts) or 1
    forwards = sum(1 for post in posts if post.get("is_forward"))
    linked = sum(1 for post in posts if post.get("has_link"))
    forward_share = round(forwards / total, 3)
    link_share = round(linked / total, 3)
    cadence = round(len(posts) / _hours_span(posts), 2)
    derived = KIND_AGGREGATOR if (forward_share >= 0.25 or link_share < 0.2) else KIND_PRIMARY
    kind = kind_hint if kind_hint in (KIND_PRIMARY, KIND_AGGREGATOR, KIND_SHILL, KIND_SECURITY) else derived
    mean_score = sum(int(post.get("trust_score", 0)) for post in posts) / total
    trust = mean_score + (-10 if kind == KIND_AGGREGATOR else 5 if link_share > 0.5 else 0)
    trust -= 10 if cadence > 6 else 5 if cadence > 2 else 0
    trust = max(0, min(100, round(trust)))
    return {
        "kind": kind,
        "forward_share": forward_share,
        "link_share": link_share,
        "cadence_per_hour": cadence,
        "posts_scored": len(posts),
        "trust_score": trust,
        "trust_band": trust_band(trust),
        "trust_weight": KIND_WEIGHTS.get(kind, 0.5),
    }


def annotate(posts: list[dict[str, Any]], kind_hint: str | None = None) -> list[dict[str, Any]]:
    """Score every post with the channel kind applied to the rubric."""
    kind = kind_hint or "unknown"
    return [score_post(post, kind) for post in posts]
