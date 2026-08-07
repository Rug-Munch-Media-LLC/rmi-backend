"""Postiz Premium Domain — 30+ Platform Cross-Posting.

DegenFeed's premium cross-posting engine. More platforms than Hootsuite (20)
or Buffer (8). Web3-native with 8+ decentralized protocols.

Architecture:
  - Postgres for persistent storage (platform connections, scheduled posts)
  - In-memory store for fast operation (production: Redis + Postgres)
  - Platform definitions in platforms.py (single source of truth)
  - Service layer in service.py (business logic, scheduling, analytics)
  - Router in router.py (20+ REST endpoints at /api/v1/postiz/*)

Platform categories:
  - Social: Twitter/X, Facebook, Instagram, LinkedIn, Pinterest, TikTok
  - Video: YouTube, TikTok, Instagram Reels
  - Professional: LinkedIn articles, Medium, DEV.to, Hashnode
  - Blogging: WordPress, Blogger, Ghost, Tumblr
  - Forums: Reddit, Discord (webhook)
  - Messaging: Telegram (channels), Slack
  - Newsletter: Substack, Revue, Mailchimp
  - Decentralized: Nostr, Farcaster, Bluesky, Mastodon, Lens, PeerTube, Pixelfed, WriteFreely

Total: 30+ platforms — market-leading coverage.
"""

from __future__ import annotations

from app.core import health as health_mod
from app.core.health import DomainHealth
from app.domains.postiz.platforms import PLATFORMS, platform_summary


async def _health_check() -> DomainHealth:
    """Postiz domain health: platforms loaded, store available."""
    try:
        from app.domains.postiz.service import get_store

        get_store()
        summary = platform_summary()
        return DomainHealth(
            name="postiz",
            healthy=True,
            details={
                "platforms": summary["total"],
                "decentralized": summary["decentralized_count"],
                "categories": summary["by_category"],
                "store": "available",
            },
        )
    except Exception as e:
        return DomainHealth(name="postiz", healthy=False, error=str(e))


health_mod.register_health_check("postiz", _health_check)

from app.domains.postiz.router import router  # noqa: E402 — optional dependency guarded by try/except
from app.domains.postiz.service import get_store  # noqa: E402 — module docstring precedes import block

__all__ = [
    "get_store",
    "router",
]
