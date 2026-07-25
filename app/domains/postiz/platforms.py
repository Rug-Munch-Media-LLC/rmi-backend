"""Postiz Platform Definitions — 30+ Platforms.

Single source of truth for every supported platform. Each platform has:
  - id: machine-readable slug
  - name: human-readable name
  - category: social, video, professional, blogging, forum, messaging, newsletter, decentralized
  - max_length: character limit
  - media_types: supported media formats
  - oauth: whether OAuth is required
  - webhook: whether webhook posting is supported
  - decentralized: whether it's a decentralized/federated protocol
  - icon: emoji icon
  - docs_url: link to API docs
  - status: active, beta, planned
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


# ── Platform definition ──────────────────────────────────────────────


class PlatformDefinition(BaseModel):
    """A single platform that can be posted to."""

    id: str = Field(..., description="Machine-readable slug")
    name: str = Field(..., description="Human-readable name")
    category: str = Field(
        ..., description="social, video, professional, blogging, forum, messaging, newsletter, decentralized"
    )
    max_length: int = Field(280, description="Maximum character limit")
    media_types: list[str] = Field(default_factory=lambda: ["text"], description="Supported media types")
    oauth: bool = Field(True, description="OAuth required")
    webhook: bool = Field(False, description="Webhook posting supported")
    decentralized: bool = Field(False, description="Decentralized/federated protocol")
    icon: str = Field("🌐", description="Platform icon emoji")
    docs_url: str = Field("", description="API docs URL")
    status: str = Field("active", description="active, beta, planned")


# ── All platforms ────────────────────────────────────────────────────

PLATFORMS: dict[str, PlatformDefinition] = {
    # ── Social ───────────────────────────────────────────────────────
    "twitter": PlatformDefinition(
        id="twitter",
        name="Twitter / X",
        category="social",
        max_length=280,
        media_types=["text", "image", "video", "poll"],
        oauth=True,
        icon="🐦",
        docs_url="https://developer.twitter.com",
    ),
    "facebook": PlatformDefinition(
        id="facebook",
        name="Facebook",
        category="social",
        max_length=63206,
        media_types=["text", "image", "video", "link"],
        oauth=True,
        icon="📘",
        docs_url="https://developers.facebook.com",
    ),
    "instagram": PlatformDefinition(
        id="instagram",
        name="Instagram",
        category="social",
        max_length=2200,
        media_types=["image", "video", "carousel"],
        oauth=True,
        icon="📸",
        docs_url="https://developers.instagram.com",
    ),
    "linkedin": PlatformDefinition(
        id="linkedin",
        name="LinkedIn",
        category="social",
        max_length=3000,
        media_types=["text", "image", "video", "document"],
        oauth=True,
        icon="💼",
        docs_url="https://developers.linkedin.com",
    ),
    "pinterest": PlatformDefinition(
        id="pinterest",
        name="Pinterest",
        category="social",
        max_length=500,
        media_types=["image", "video"],
        oauth=True,
        icon="📌",
        docs_url="https://developers.pinterest.com",
    ),
    "tiktok": PlatformDefinition(
        id="tiktok",
        name="TikTok",
        category="social",
        max_length=2200,
        media_types=["video"],
        oauth=True,
        icon="🎵",
        docs_url="https://developers.tiktok.com",
    ),
    # ── Video ────────────────────────────────────────────────────────
    "youtube": PlatformDefinition(
        id="youtube",
        name="YouTube",
        category="video",
        max_length=5000,
        media_types=["video", "short"],
        oauth=True,
        icon="▶️",
        docs_url="https://developers.google.com/youtube",
    ),
    "instagram_reels": PlatformDefinition(
        id="instagram_reels",
        name="Instagram Reels",
        category="video",
        max_length=2200,
        media_types=["video"],
        oauth=True,
        icon="🎬",
        docs_url="https://developers.instagram.com",
    ),
    # ── Professional ─────────────────────────────────────────────────
    "linkedin_articles": PlatformDefinition(
        id="linkedin_articles",
        name="LinkedIn Articles",
        category="professional",
        max_length=125000,
        media_types=["text", "image", "video"],
        oauth=True,
        icon="📝",
        docs_url="https://developers.linkedin.com",
    ),
    "medium": PlatformDefinition(
        id="medium",
        name="Medium",
        category="professional",
        max_length=15000,
        media_types=["text", "image"],
        oauth=True,
        icon="✍️",
        docs_url="https://medium.com/@api",
    ),
    "devto": PlatformDefinition(
        id="devto",
        name="DEV.to",
        category="professional",
        max_length=64000,
        media_types=["text", "image", "video"],
        oauth=True,
        icon="💻",
        docs_url="https://developers.forem.com",
    ),
    "hashnode": PlatformDefinition(
        id="hashnode",
        name="Hashnode",
        category="professional",
        max_length=64000,
        media_types=["text", "image", "video"],
        oauth=True,
        icon="🧑‍💻",
        docs_url="https://apidocs.hashnode.com",
    ),
    # ── Blogging ─────────────────────────────────────────────────────
    "wordpress": PlatformDefinition(
        id="wordpress",
        name="WordPress",
        category="blogging",
        max_length=99999,
        media_types=["text", "image", "video"],
        oauth=True,
        icon="📰",
        docs_url="https://developer.wordpress.org",
    ),
    "blogger": PlatformDefinition(
        id="blogger",
        name="Blogger",
        category="blogging",
        max_length=99999,
        media_types=["text", "image"],
        oauth=True,
        icon="📓",
        docs_url="https://developers.google.com/blogger",
    ),
    "ghost": PlatformDefinition(
        id="ghost",
        name="Ghost",
        category="blogging",
        max_length=99999,
        media_types=["text", "image", "video"],
        oauth=False,
        icon="👻",
        docs_url="https://ghost.org/docs",
    ),
    "tumblr": PlatformDefinition(
        id="tumblr",
        name="Tumblr",
        category="blogging",
        max_length=99999,
        media_types=["text", "image", "video", "audio"],
        oauth=True,
        icon="💭",
        docs_url="https://www.tumblr.com/docs/en/api",
    ),
    # ── Forums ───────────────────────────────────────────────────────
    "reddit": PlatformDefinition(
        id="reddit",
        name="Reddit",
        category="forum",
        max_length=40000,
        media_types=["text", "image", "video", "link"],
        oauth=True,
        icon="🤖",
        docs_url="https://www.reddit.com/dev/api",
    ),
    "discord": PlatformDefinition(
        id="discord",
        name="Discord",
        category="forum",
        max_length=2000,
        media_types=["text", "image", "video", "embed"],
        oauth=False,
        webhook=True,
        icon="💬",
        docs_url="https://discord.com/developers",
    ),
    # ── Messaging ────────────────────────────────────────────────────
    "telegram": PlatformDefinition(
        id="telegram",
        name="Telegram",
        category="messaging",
        max_length=4096,
        media_types=["text", "image", "video", "document", "poll"],
        oauth=False,
        icon="✈️",
        docs_url="https://core.telegram.org/bots/api",
    ),
    "slack": PlatformDefinition(
        id="slack",
        name="Slack",
        category="messaging",
        max_length=40000,
        media_types=["text", "image", "video", "file"],
        oauth=True,
        webhook=True,
        icon="💠",
        docs_url="https://api.slack.com",
    ),
    # ── Newsletter ───────────────────────────────────────────────────
    "substack": PlatformDefinition(
        id="substack",
        name="Substack",
        category="newsletter",
        max_length=99999,
        media_types=["text", "image", "video"],
        oauth=True,
        icon="📬",
        docs_url="https://substack.com/api",
    ),
    "revue": PlatformDefinition(
        id="revue",
        name="Revue",
        category="newsletter",
        max_length=99999,
        media_types=["text", "image"],
        oauth=True,
        icon="📧",
        docs_url="https://www.getrevue.co/api",
    ),
    "mailchimp": PlatformDefinition(
        id="mailchimp",
        name="Mailchimp",
        category="newsletter",
        max_length=99999,
        media_types=["text", "image", "video"],
        oauth=True,
        icon="🐵",
        docs_url="https://mailchimp.com/developer",
    ),
    # ── Decentralized Protocols ──────────────────────────────────────
    "nostr": PlatformDefinition(
        id="nostr",
        name="Nostr",
        category="decentralized",
        max_length=64000,
        media_types=["text", "image", "video"],
        oauth=False,
        decentralized=True,
        icon="🧅",
        docs_url="https://github.com/nostr-protocol/nostr",
    ),
    "farcaster": PlatformDefinition(
        id="farcaster",
        name="Farcaster",
        category="decentralized",
        max_length=1024,
        media_types=["text", "image", "video"],
        oauth=True,
        decentralized=True,
        icon="🪐",
        docs_url="https://docs.farcaster.xyz",
    ),
    "bluesky": PlatformDefinition(
        id="bluesky",
        name="Bluesky",
        category="decentralized",
        max_length=300,
        media_types=["text", "image", "video"],
        oauth=True,
        decentralized=True,
        icon="☀️",
        docs_url="https://docs.bsky.app",
    ),
    "mastodon": PlatformDefinition(
        id="mastodon",
        name="Mastodon",
        category="decentralized",
        max_length=500,
        media_types=["text", "image", "video"],
        oauth=True,
        decentralized=True,
        icon="🐘",
        docs_url="https://docs.joinmastodon.org",
    ),
    "lens": PlatformDefinition(
        id="lens",
        name="Lens Protocol",
        category="decentralized",
        max_length=5000,
        media_types=["text", "image", "video"],
        oauth=True,
        decentralized=True,
        icon="🔍",
        docs_url="https://docs.lens.xyz",
    ),
    "peertube": PlatformDefinition(
        id="peertube",
        name="PeerTube",
        category="decentralized",
        max_length=1000,
        media_types=["video"],
        oauth=True,
        decentralized=True,
        icon="🎥",
        docs_url="https://docs.joinpeertube.org",
    ),
    "pixelfed": PlatformDefinition(
        id="pixelfed",
        name="Pixelfed",
        category="decentralized",
        max_length=500,
        media_types=["image", "video"],
        oauth=True,
        decentralized=True,
        icon="📷",
        docs_url="https://docs.pixelfed.org",
    ),
    "writefreely": PlatformDefinition(
        id="writefreely",
        name="WriteFreely",
        category="decentralized",
        max_length=99999,
        media_types=["text"],
        oauth=True,
        decentralized=True,
        icon="✏️",
        docs_url="https://writefreely.org",
    ),
}


# ── Helpers ──────────────────────────────────────────────────────────


def get_platform(platform_id: str) -> PlatformDefinition | None:
    """Get platform by ID. Case-insensitive."""
    return PLATFORMS.get(platform_id.lower())


def get_platforms_by_category(category: str) -> list[PlatformDefinition]:
    """Get all platforms in a category."""
    return [p for p in PLATFORMS.values() if p.category == category]


def get_decentralized_platforms() -> list[PlatformDefinition]:
    """Get all decentralized/federated platforms."""
    return [p for p in PLATFORMS.values() if p.decentralized]


def get_oauth_platforms() -> list[PlatformDefinition]:
    """Get all platforms requiring OAuth."""
    return [p for p in PLATFORMS.values() if p.oauth]


def get_webhook_platforms() -> list[PlatformDefinition]:
    """Get all platforms supporting webhook posting."""
    return [p for p in PLATFORMS.values() if p.webhook]


def platform_summary() -> dict[str, Any]:
    """Return a summary of all platforms grouped by category."""
    categories: dict[str, list[dict[str, Any]]] = {}
    for platform in PLATFORMS.values():
        cat = platform.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(
            {
                "id": platform.id,
                "name": platform.name,
                "decentralized": platform.decentralized,
                "status": platform.status,
            }
        )
    return {
        "total": len(PLATFORMS),
        "by_category": {cat: len(platforms) for cat, platforms in categories.items()},
        "decentralized_count": len(get_decentralized_platforms()),
        "categories": categories,
    }
