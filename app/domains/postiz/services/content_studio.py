from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.domains.postiz.platforms import get_platform
from app.domains.postiz.services.store import get_store

logger = get_logger(__name__)


def compose_preview(request_data: dict[str, Any]) -> dict[str, Any]:
    """Generate previews of a post across all target platforms."""
    text = request_data.get("text", "")
    platform_ids = [p.lower() for p in request_data.get("platforms", [])]
    media_ids = request_data.get("media_ids", [])
    media_urls = request_data.get("media_urls", [])
    tags = request_data.get("tags", [])
    thread = request_data.get("thread", [])

    previews = []
    all_within_limits = True
    all_warnings = []

    for pid in platform_ids:
        pdef = get_platform(pid)
        if not pdef:
            continue

        formatted = _format_for_platform(text, pid, tags)
        char_count = len(text)  # Base text length
        thread_char_count = sum(len(t) for t in thread)  # Thread length
        total_chars = char_count + thread_char_count
        within_limit = total_chars <= pdef.max_length

        if not within_limit:
            all_within_limits = False

        media_count = len(media_ids) + len(media_urls)
        media_accepted = True
        warnings = []

        if not within_limit:
            warnings.append(
                f"Post exceeds {pdef.name}'s {pdef.max_length} character limit by {total_chars - pdef.max_length} characters"
            )
        if media_count > 10 and not pdef.decentralized:
            warnings.append(f"{pdef.name} supports a maximum of 10 media items")
        if thread and pid == "twitter" and len(thread) > 25:
            warnings.append("Twitter thread exceeds 25 posts")

        preview = {
            "platform_id": pid,
            "platform_name": pdef.name,
            "icon": pdef.icon,
            "formatted_text": formatted,
            "character_count": total_chars,
            "max_length": pdef.max_length,
            "within_limit": within_limit,
            "media_count": media_count,
            "media_accepted": media_accepted,
            "warnings": warnings,
            "preview_html": None,  # Would contain platform-specific HTML preview
        }
        previews.append(preview)
        all_warnings.extend(warnings)

    # Generate hashtag suggestions
    suggestions = _generate_hashtag_suggestions(tags, text, platform_ids)

    return {
        "previews": previews,
        "all_within_limits": all_within_limits,
        "warnings": all_warnings[:10],
        "suggestions": suggestions,
    }


def _format_for_platform(text: str, platform_id: str, tags: list[str]) -> str:
    """Format text appropriately for each platform."""
    pdef = get_platform(platform_id)
    if not pdef:
        return text

    # Truncate if needed
    if len(text) > pdef.max_length:
        text = text[: pdef.max_length - 3] + "..."

    # Add hashtags for platforms that use them
    if platform_id in ("twitter", "instagram", "tiktok", "linkedin", "nostr", "bluesky", "mastodon"):
        tag_str = " ".join(f"#{t.replace(' ', '')}" for t in tags)
        if tag_str:
            text = f"{text}\n\n{tag_str}"

    return text


def _generate_hashtag_suggestions(
    tags: list[str],
    text: str,
    platform_ids: list[str],
) -> dict[str, Any]:
    """Generate AI hashtag suggestions per platform."""
    # Extract keywords from text (simple implementation)
    words = text.lower().split()
    keywords = [w for w in words if len(w) > 3 and w.isalpha()][:5]

    platform_suggestions = {}
    for pid in platform_ids:
        pdef = get_platform(pid)
        if not pdef:
            continue
        # Platform-specific hashtag recommendations
        base_tags = [f"{kw}" for kw in keywords]
        trending = {
            "twitter": ["crypto", "web3", "blockchain", "defi", "nft"],
            "instagram": ["crypto", "web3", "blockchain", "cryptocurrency", "bitcoin"],
            "tiktok": ["crypto", "blockchain", "web3", "defi", "cryptotok"],
            "nostr": ["nostr", "plebchain", "bitcoin", "proofofwork"],
            "farcaster": ["farcaster", "fc", "degen"],
            "bluesky": ["bsky", "atprotocol"],
        }.get(pid, ["crypto", "web3", "blockchain"])

        platform_suggestions[pid] = list(set(base_tags + trending[:5]))

    return {
        "platform_suggestions": platform_suggestions,
        "trending_tags": ["crypto", "web3", "blockchain", "defi", "ai"],
        "recommended": ["crypto", "web3", "blockchain"],
    }


def upload_media(
    filename: str,
    media_type: str,
    mime_type: str,
    size_bytes: int,
) -> dict[str, Any]:
    """Upload media to the library."""
    store = get_store()

    media = store.add_media(
        {
            "filename": filename,
            "url": f"/media/{uuid.uuid4().hex}/{filename}",
            "thumbnail_url": "",
            "media_type": media_type,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "width": 0,
            "height": 0,
            "duration_seconds": 0.0,
            "alt_text": "",
            "tags": [],
            "used_in_posts": 0,
        }
    )

    return {
        "media": media,
        "upload_url": media["url"],
        "upload_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }


def get_media_library(
    media_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get media library contents."""
    store = get_store()
    items = store.get_all_media(media_type=media_type, limit=limit, offset=offset)

    by_type: dict[str, int] = defaultdict(int)
    total_size = 0
    for m in items:
        by_type[m.get("media_type", "unknown")] += 1
        total_size += m.get("size_bytes", 0)

    return {
        "items": items,
        "total": len(store._media_items),
        "total_size_bytes": total_size,
        "by_type": dict(by_type),
    }
