"""Multi-platform social publishing for RugMunchBot.

A thin, fail-open abstraction over the platforms the bot can post to:
Telegram (real), Farcaster (real, Neynar) and X / Mastodon / Discord
(documented stubs ready for real clients).

See :mod:`app.domains.social.publisher` for the full contract and
``docs/SOCIAL_PUBLISHER.md`` for per-platform configuration.
"""

from __future__ import annotations

from app.domains.social.publisher import (
    ALL_PLATFORMS,
    DiscordPublisher,
    FarcasterPublisher,
    MastodonPublisher,
    PlatformAdapter,
    PostizPublisher,
    SocialPublisher,
    TelegramPublisher,
    XPublisher,
    default_adapters,
    social_publisher,
)

__all__ = [
    "ALL_PLATFORMS",
    "DiscordPublisher",
    "FarcasterPublisher",
    "MastodonPublisher",
    "PlatformAdapter",
    "PostizPublisher",
    "SocialPublisher",
    "TelegramPublisher",
    "XPublisher",
    "default_adapters",
    "social_publisher",
]
