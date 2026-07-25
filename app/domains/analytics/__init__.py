"""Creator Analytics Domain — True Metrics, Cross-Protocol, Content Improvement.

Five analytics pillars for creators:
  1. True view counts (unique viewers, watch time, drop-off, VTR)
  2. Engagement analytics (likes, comments, tips, CTR)
  3. Cross-protocol analytics (Nostr, Farcaster, Bluesky, Mastodon)
  4. Content improvement insights (AI-driven pattern detection)
  5. Audience analytics (growth, demographics, churn, sources)

Design:
  - Postgres for raw event storage (auditable, true counts)
  - In-memory ingestion buffer for sub-second writes
  - Every view count is DEDUPLICATED by fingerprint + post_id + session_id
  - No "X-style inflation" — every number is a real human
  - Cross-protocol data is comparable (same counting methodology)
  - Insights are statistical, not hallucinated
"""

from __future__ import annotations

from app.core import health as health_mod
from app.core.health import DomainHealth


async def _health_check() -> DomainHealth:
    """Analytics domain health: engine available."""
    try:
        from app.domains.analytics.service import get_analytics_engine

        engine = get_analytics_engine()
        return DomainHealth(
            name="analytics",
            healthy=True,
            details={
                "engine": "available",
                "protocols": ["nostr", "farcaster", "bluesky", "mastodon", "direct"],
            },
        )
    except Exception as e:
        return DomainHealth(name="analytics", healthy=False, error=str(e))


health_mod.register_health_check("analytics", _health_check)

from app.domains.analytics.router import router
from app.domains.analytics.service import CreatorAnalyticsEngine, get_analytics_engine

__all__ = [
    "CreatorAnalyticsEngine",
    "get_analytics_engine",
    "router",
]
