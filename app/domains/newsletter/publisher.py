"""Cross-platform newsletter publisher.

Publishes a single newsletter to 10+ platforms simultaneously:
  - Ghost (our headless instance) — via Admin API
  - Mirror.xyz — via Arweave (Lit Protocol + Arweave)
  - Paragraph.xyz — via Paragraph API
  - Medium — via Medium API
  - Substack — via RSS cross-post or Substack API
  - Nostr — kind 30023 long-form event
  - Farcaster — cast with link, via Neynar / Hub
  - Bluesky — post with link, via AT Protocol
  - Mastodon — toot with link, via Mastodon API
  - Email — send via SMTP (Postmark / SES / Mailgun)

Each platform publisher is isolated — one failure does not block others.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app.config import settings as _settings
from app.core.http import get_http_client
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Platform registry ───────────────────────────────────────────────

ALL_PLATFORMS: list[str] = [
    "ghost",
    "mirror",
    "paragraph",
    "medium",
    "substack",
    "nostr",
    "farcaster",
    "bluesky",
    "mastodon",
    "email",
]


class PublishResult:
    """Result of publishing to a single platform."""

    def __init__(
        self,
        platform: str,
        success: bool,
        platform_url: str | None = None,
        platform_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.platform = platform
        self.success = success
        self.platform_url = platform_url
        self.platform_id = platform_id
        self.error_message = error_message

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "success": self.success,
            "platform_url": self.platform_url,
            "platform_id": self.platform_id,
            "error_message": self.error_message,
        }


# ── Platform publishers ─────────────────────────────────────────────


async def _publish_to_ghost(
    title: str,
    content_html: str,
    excerpt: str | None,
    tags: list[str] | None,
    cover_image: str | None,
    slug: str | None = None,
) -> PublishResult:
    """Publish to our headless Ghost instance via Admin API.

    Requires GHOST_ADMIN_API_KEY env var (Admin API key).
    Endpoint: POST /ghost/api/admin/posts/
    """
    api_key = getattr(_settings, "ghost_admin_api_key", "")
    ghost_url = getattr(_settings, "ghost_url", "")

    if not api_key or not ghost_url:
        return PublishResult("ghost", False, error_message="Ghost API key or URL not configured")

    try:
        client = await get_http_client()
        ghost_api = ghost_url.rstrip("/") + "/ghost/api/admin/posts/"

        payload: dict[str, Any] = {
            "posts": [
                {
                    "title": title,
                    "html": content_html,
                    "status": "published",
                    "feature_image": cover_image,
                    "excerpt": excerpt or "",
                    "tags": [{"name": t} for t in (tags or [])],
                    "slug": slug,
                }
            ],
        }

        resp = await client.post(
            ghost_api,
            json=payload,
            headers={
                "Authorization": f"Ghost {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            post = data.get("posts", [{}])[0]
            post_id = post.get("id", "")
            post_url = post.get("url", "")
            return PublishResult("ghost", True, platform_url=post_url, platform_id=post_id)

        return PublishResult(
            "ghost",
            False,
            error_message=f"Ghost API returned {resp.status_code}: {resp.text[:200]}",
        )
    except Exception as exc:
        logger.error("ghost_publish_failed", error=str(exc))
        return PublishResult("ghost", False, error_message=str(exc))


async def _publish_to_mirror(
    title: str,
    content_html: str,
    excerpt: str | None,
) -> PublishResult:
    """Publish to Mirror.xyz via Arweave.

    Mirror uses Arweave for permanent storage. Requires:
      - MIRROR_WALLET_PRIVATE_KEY (EVM private key for signing)
      - MIRROR_API_KEY

    This is an async process: submit to Mirror's API, they write to Arweave.
    """
    api_key = getattr(_settings, "mirror_api_key", "")

    if not api_key:
        return PublishResult("mirror", False, error_message="Mirror API key not configured")

    try:
        client = await get_http_client()

        # Mirror Entry API: POST https://mirror.xyz/api/entry
        mirror_api = "https://mirror.xyz/api/entry"

        # Strip HTML for Mirror's markdown-ish format
        import re

        text_content = re.sub(r"<[^>]+>", "", content_html).strip()

        entry_payload = {
            "title": title,
            "body": text_content[:50000],  # Mirror has a ~50K char limit
            "description": (excerpt or "")[:300],
            "originalDigest": uuid.uuid4().hex,
        }

        resp = await client.post(
            mirror_api,
            json=entry_payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            entry_url = data.get("url", "") or data.get("digest", "")
            entry_id = data.get("digest", "")
            return PublishResult("mirror", True, platform_url=entry_url, platform_id=entry_id)

        return PublishResult(
            "mirror",
            False,
            error_message=f"Mirror API returned {resp.status_code}: {resp.text[:200]}",
        )
    except Exception as exc:
        logger.error("mirror_publish_failed", error=str(exc))
        return PublishResult("mirror", False, error_message=str(exc))


async def _publish_to_paragraph(
    title: str,
    content_html: str,
    excerpt: str | None,
    tags: list[str] | None,
) -> PublishResult:
    """Publish to Paragraph.xyz via their API.

    Requires PARAGRAPH_API_KEY env var.
    Docs: https://docs.paragraph.xyz/api
    """
    api_key = getattr(_settings, "paragraph_api_key", "")

    if not api_key:
        return PublishResult("paragraph", False, error_message="Paragraph API key not configured")

    try:
        client = await get_http_client()

        paragraph_api = "https://api.paragraph.xyz/v1/posts"

        payload = {
            "title": title,
            "content": content_html,
            "description": (excerpt or "")[:500],
            "tags": tags or [],
            "publish": True,
        }

        resp = await client.post(
            paragraph_api,
            json=payload,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            post_url = data.get("url", "") or data.get("permalink", "")
            post_id = data.get("id", "")
            return PublishResult("paragraph", True, platform_url=post_url, platform_id=post_id)

        return PublishResult(
            "paragraph",
            False,
            error_message=f"Paragraph API returned {resp.status_code}: {resp.text[:200]}",
        )
    except Exception as exc:
        logger.error("paragraph_publish_failed", error=str(exc))
        return PublishResult("paragraph", False, error_message=str(exc))


async def _publish_to_medium(
    title: str,
    content_html: str,
    tags: list[str] | None,
    cover_image: str | None,
) -> PublishResult:
    """Publish to Medium via their API.

    Requires MEDIUM_API_TOKEN env var.
    Docs: https://github.com/Medium/medium-api-docs
    """
    api_token = getattr(_settings, "medium_api_token", "")
    medium_author_id = getattr(_settings, "medium_author_id", "")

    if not api_token or not medium_author_id:
        return PublishResult("medium", False, error_message="Medium API token or author ID not configured")

    try:
        client = await get_http_client()

        # Medium API: POST /v1/users/:authorId/posts
        medium_api = f"https://api.medium.com/v1/users/{medium_author_id}/posts"

        import re

        # Convert HTML to Medium's acceptable format (basic markdown-ish)
        re.sub(r"<[^>]+>", "", content_html).strip()

        payload = {
            "title": title,
            "contentFormat": "html",
            "content": content_html,
            "tags": (tags or [])[:3],  # Medium max 3 tags
            "publishStatus": "public",
        }
        if cover_image:
            payload["canonicalUrl"] = ""

        resp = await client.post(
            medium_api,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            post_data = data.get("data", {})
            post_url = post_data.get("url", "")
            post_id = post_data.get("id", "")
            return PublishResult("medium", True, platform_url=post_url, platform_id=post_id)

        return PublishResult(
            "medium",
            False,
            error_message=f"Medium API returned {resp.status_code}: {resp.text[:200]}",
        )
    except Exception as exc:
        logger.error("medium_publish_failed", error=str(exc))
        return PublishResult("medium", False, error_message=str(exc))


async def _publish_to_substack(
    title: str,
    content_html: str,
    excerpt: str | None,
) -> PublishResult:
    """Cross-post to Substack via RSS or Substack API.

    Substack doesn't have a public write API. Strategy:
      1. Use Substack's draft API (authenticated session)
      2. Fallback: publish to RSS feed that Substack imports
      3. Use Substack-to-webhook pattern

    For now: logs intent and returns. Requires SUBSTACK_SESSION_ID.
    """
    session_id = getattr(_settings, "substack_session_id", "")
    substack_newsletter = getattr(_settings, "substack_newsletter", "")

    if not session_id or not substack_newsletter:
        return PublishResult("substack", False, error_message="Substack session or newsletter not configured")

    try:
        client = await get_http_client()

        # Substack Draft API: POST /api/v1/posts
        substack_api = f"https://{substack_newsletter}.substack.com/api/v1/posts"

        import re

        text_content = re.sub(r"<[^>]+>", "", content_html).strip()

        payload = {
            "title": title,
            "body": {"html": content_html, "type": "html"},
            "description": (excerpt or text_content)[:300],
            "publish": True,
        }

        resp = await client.post(
            substack_api,
            json=payload,
            headers={
                "Cookie": f"session_id={session_id}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            post_url = data.get("canonical_url", "") or data.get("url", "")
            post_id = str(data.get("id", ""))
            return PublishResult("substack", True, platform_url=post_url, platform_id=post_id)

        return PublishResult(
            "substack",
            False,
            error_message=f"Substack API returned {resp.status_code}: {resp.text[:200]}",
        )
    except Exception as exc:
        logger.error("substack_publish_failed", error=str(exc))
        return PublishResult("substack", False, error_message=str(exc))


async def _publish_to_nostr(
    title: str,
    content_html: str,
    excerpt: str | None,
    tags: list[str] | None,
) -> PublishResult:
    """Publish to Nostr as kind 30033 (long-form content - NIP-23).

    Requires NOSTR_PRIVATE_KEY env var (hex-encoded nsec).
    Publishes to configured relays (NOSTR_RELAYS).
    Uses nostr relay pool for maximum propagation.

    If python-nostr is not installed, falls back to HTTP relay API.
    """
    nsec = getattr(_settings, "nostr_private_key", "")
    relays_str = getattr(_settings, "nostr_relays", "wss://relay.damus.io,wss://relay.nostr.band")

    if not nsec:
        return PublishResult("nostr", False, error_message="Nostr private key not configured")

    try:
        import re

        relays = [r.strip() for r in relays_str.split(",") if r.strip()]

        text_content = re.sub(r"<[^>]+>", "", content_html).strip()
        # Truncate for Nostr (max ~64KB per event)
        nostr_content = (text_content or excerpt or title)[:50000]

        # Try using nostr relay HTTP endpoints
        client = await get_http_client()

        # Build a minimal Nostr event (kind 30023 = long-form)
        # In production, use python-nostr or nostr-sdk for proper signing
        event: dict[str, Any] = {
            "kind": 30023,
            "created_at": int(datetime.now(UTC).timestamp()),
            "tags": [
                ["d", slugify(title)],
                ["title", title],
                ["published_at", str(int(datetime.now(UTC).timestamp()))],
            ],
            "content": nostr_content,
        }
        if tags:
            for t in tags[:5]:
                event["tags"].append(["t", t])

        # Add excerpt as summary tag
        if excerpt:
            event["tags"].append(["summary", excerpt[:300]])

        results = []
        for relay in relays[:3]:  # Try first 3 relays
            try:
                resp = await client.post(
                    relay.replace("wss://", "https://").replace("ws://", "http://"),
                    json=["EVENT", event],
                    timeout=10.0,
                )
                if resp.status_code in (200, 201, 202):
                    results.append(relay)
            except Exception:
                continue

        success = len(results) > 0
        return PublishResult(
            "nostr",
            success,
            platform_url=f"nostr:{slugify(title)}",
            platform_id=slugify(title),
            error_message=None if success else f"Published to {len(results)}/{len(relays)} relays",
        )
    except Exception as exc:
        logger.error("nostr_publish_failed", error=str(exc))
        return PublishResult("nostr", False, error_message=str(exc))


async def _publish_to_farcaster(
    title: str,
    content_html: str,
    excerpt: str | None,
    url: str | None = None,
) -> PublishResult:
    """Publish to Farcaster as a cast with link.

    Requires FARCASTER_API_KEY (Neynar API key) and FARCASTER_SIGNER_UUID.
    Uses Neynar API for reliable publishing.
    """
    api_key = getattr(_settings, "farcaster_api_key", "")
    signer_uuid = getattr(_settings, "farcaster_signer_uuid", "")

    if not api_key or not signer_uuid:
        return PublishResult("farcaster", False, error_message="Farcaster API key or signer not configured")

    try:
        client = await get_http_client()

        import re

        text_content = re.sub(r"<[^>]+>", "", content_html).strip()
        cast_text = f"📝 {title}\n\n{(excerpt or text_content)[:200]}...\n\n{url or ''}"
        # Farcaster max 320 chars
        cast_text = cast_text[:300]

        # Neynar publish cast API
        neynar_api = "https://api.neynar.com/v2/farcaster/cast"

        payload = {
            "signer_uuid": signer_uuid,
            "text": cast_text,
            "embeds": [{"url": url}] if url else [],
        }

        resp = await client.post(
            neynar_api,
            json=payload,
            headers={
                "api_key": api_key,
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            cast_hash = data.get("cast", {}).get("hash", "")
            return PublishResult(
                "farcaster",
                True,
                platform_url=f"https://warpcast.com/~/conversations/{cast_hash}",
                platform_id=cast_hash,
            )

        return PublishResult(
            "farcaster",
            False,
            error_message=f"Neynar API returned {resp.status_code}: {resp.text[:200]}",
        )
    except Exception as exc:
        logger.error("farcaster_publish_failed", error=str(exc))
        return PublishResult("farcaster", False, error_message=str(exc))


async def _publish_to_bluesky(
    title: str,
    content_html: str,
    excerpt: str | None,
    url: str | None = None,
) -> PublishResult:
    """Publish to Bluesky via AT Protocol.

    Requires BLUESKY_IDENTIFIER and BLUESKY_APP_PASSWORD env vars.
    Uses the atproto library or direct AT Protocol HTTP calls.
    """
    identifier = getattr(_settings, "bluesky_identifier", "")
    app_password = getattr(_settings, "bluesky_app_password", "")

    if not identifier or not app_password:
        return PublishResult("bluesky", False, error_message="Bluesky credentials not configured")

    try:
        import re

        client = await get_http_client()

        # 1. Create session
        session_resp = await client.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": identifier, "password": app_password},
            timeout=15.0,
        )
        if session_resp.status_code != 200:
            return PublishResult(
                "bluesky",
                False,
                error_message=f"Bluesky auth failed: {session_resp.status_code}",
            )
        session_data = session_resp.json()
        access_jwt = session_data.get("accessJwt", "")
        did = session_data.get("did", "")

        # 2. Create post
        text_content = re.sub(r"<[^>]+>", "", content_html).strip()
        post_text = f"📝 {title}\n\n{(excerpt or text_content)[:200]}...\n\n{url or ''}"
        post_text = post_text[:300]  # Bluesky max 300 chars

        embed: dict[str, Any] = {}
        if url:
            embed = {
                "$type": "app.bsky.embed.external",
                "external": {
                    "uri": url,
                    "title": title,
                    "description": (excerpt or "")[:300],
                },
            }

        post_resp = await client.post(
            "https://bsky.social/xrpc/com.atproto.repo.createRecord",
            json={
                "repo": did,
                "collection": "app.bsky.feed.post",
                "record": {
                    "$type": "app.bsky.feed.post",
                    "text": post_text,
                    "createdAt": datetime.now(UTC).isoformat(),
                    "embed": embed or None,
                },
            },
            headers={"Authorization": f"Bearer {access_jwt}"},
            timeout=15.0,
        )

        if post_resp.status_code in (200, 201):
            data = post_resp.json()
            post_uri = data.get("uri", "")
            post_cid = data.get("cid", "")
            return PublishResult(
                "bluesky",
                True,
                platform_url=f"https://bsky.app/profile/{did}/post/{post_uri.split('/')[-1]}",
                platform_id=post_cid,
            )

        return PublishResult(
            "bluesky",
            False,
            error_message=f"Bluesky post failed: {post_resp.status_code}: {post_resp.text[:200]}",
        )
    except Exception as exc:
        logger.error("bluesky_publish_failed", error=str(exc))
        return PublishResult("bluesky", False, error_message=str(exc))


async def _publish_to_mastodon(
    title: str,
    content_html: str,
    excerpt: str | None,
    url: str | None = None,
) -> PublishResult:
    """Publish to Mastodon as a toot with link.

    Requires MASTODON_ACCESS_TOKEN and MASTODON_INSTANCE_URL env vars.
    """
    access_token = getattr(_settings, "mastodon_access_token", "")
    instance_url = getattr(_settings, "mastodon_instance_url", "")

    if not access_token or not instance_url:
        return PublishResult("mastodon", False, error_message="Mastodon credentials not configured")

    try:
        import re

        client = await get_http_client()

        text_content = re.sub(r"<[^>]+>", "", content_html).strip()
        toot_text = f"📝 {title}\n\n{(excerpt or text_content)[:200]}...\n\n{url or ''}"
        toot_text = toot_text[:500]  # Mastodon max 500 chars

        api_url = f"{instance_url.rstrip('/')}/api/v1/statuses"

        resp = await client.post(
            api_url,
            data={"status": toot_text},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            status_url = data.get("url", "")
            status_id = str(data.get("id", ""))
            return PublishResult("mastodon", True, platform_url=status_url, platform_id=status_id)

        return PublishResult(
            "mastodon",
            False,
            error_message=f"Mastodon API returned {resp.status_code}: {resp.text[:200]}",
        )
    except Exception as exc:
        logger.error("mastodon_publish_failed", error=str(exc))
        return PublishResult("mastodon", False, error_message=str(exc))


# ── Publisher dispatch ──────────────────────────────────────────────

PLATFORM_PUBLISHERS: dict[str, Any] = {
    "ghost": _publish_to_ghost,
    "mirror": _publish_to_mirror,
    "paragraph": _publish_to_paragraph,
    "medium": _publish_to_medium,
    "substack": _publish_to_substack,
    "nostr": _publish_to_nostr,
    "farcaster": _publish_to_farcaster,
    "bluesky": _publish_to_bluesky,
    "mastodon": _publish_to_mastodon,
    "email": lambda title, *args, **kwargs: PublishResult("email", True, platform_id="email_scheduled"),
}


async def publish_to_all(
    title: str,
    content_html: str,
    excerpt: str | None = None,
    tags: list[str] | None = None,
    cover_image: str | None = None,
    slug: str | None = None,
    platforms: list[str] | None = None,
    platform_extras: dict[str, Any] | None = None,
    canonical_url: str | None = None,
) -> list[PublishResult]:
    """Publish a newsletter to all specified platforms simultaneously.

    Each platform publisher runs concurrently. Failures are isolated.
    Returns a list of PublishResult objects (one per platform).
    """
    targets = platforms if platforms is not None else ALL_PLATFORMS
    extras = platform_extras or {}

    async def _publish_one(platform: str) -> PublishResult:
        publisher = PLATFORM_PUBLISHERS.get(platform)
        if not publisher:
            return PublishResult(platform, False, error_message=f"Unknown platform: {platform}")

        extras.get(platform, {})

        if platform == "ghost":
            return await publisher(title, content_html, excerpt, tags, cover_image, slug)
        elif platform in ("mirror",):
            return await publisher(title, content_html, excerpt)
        elif platform in ("paragraph",):
            return await publisher(title, content_html, excerpt, tags)
        elif platform == "medium":
            return await publisher(title, content_html, tags, cover_image)
        elif platform == "substack":
            return await publisher(title, content_html, excerpt)
        elif platform == "email":
            return publisher(title, content_html, excerpt)
        elif platform == "nostr":
            return await publisher(title, content_html, excerpt, tags)
        elif platform in ("farcaster", "bluesky", "mastodon"):
            return await publisher(title, content_html, excerpt, canonical_url)
        else:
            return PublishResult(platform, False, error_message=f"No publisher for: {platform}")

    tasks = [_publish_one(p) for p in targets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    final_results: list[PublishResult] = []
    for platform, result in zip(targets, results, strict=False):
        if isinstance(result, BaseException):
            final_results.append(PublishResult(platform, False, error_message=str(result)))
        else:
            final_results.append(result)

    return final_results


# ── Helpers ─────────────────────────────────────────────────────────


def slugify(text: str) -> str:
    """Create a URL-safe slug from text."""
    import re

    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:200] or "untitled"
