"""Integration tests for cross-protocol reposting.

Tests the ability to repost content between different protocol identities
(Nostr, Farcaster, Bluesky, Lens, Twitter).

Follows the same patterns as test_dm_bridge.py and test_api_flows.py.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}


# ── Cross-Protocol Repost Engine ──────────────────────────────────


class CrossProtocolRepostEngine:
    """Engine for reposting content across protocol boundaries."""

    PROTOCOL_NAMES = {
        "nostr": "Nostr",
        "farcaster": "Farcaster",
        "bluesky": "Bluesky",
        "lens": "Lens",
        "twitter": "Twitter/X",
    }

    def __init__(self) -> None:
        self._reposts: list[dict[str, Any]] = []
        self._engagements: dict[str, dict[str, int]] = {}  # post_id -> {protocol: count}

    def validate_repost(self, source_protocol: str, target_protocol: str, content: str) -> list[str]:
        """Validate whether a repost is valid between protocols."""
        errors: list[str] = []

        if source_protocol not in self.PROTOCOL_NAMES:
            errors.append(f"Unknown source protocol: {source_protocol}")
        if target_protocol not in self.PROTOCOL_NAMES:
            errors.append(f"Unknown target protocol: {target_protocol}")
        if source_protocol == target_protocol:
            errors.append("Source and target protocols must differ")
        if not content or not content.strip():
            errors.append("Content cannot be empty")

        return errors

    def repost(
        self,
        source_protocol: str,
        target_protocol: str,
        original_post_id: str,
        original_author: str,
        content: str,
        reposter: str,
    ) -> dict[str, Any]:
        """Repost content from one protocol to another."""
        errors = self.validate_repost(source_protocol, target_protocol, content)
        if errors:
            raise ValueError("; ".join(errors))

        repost = {
            "id": f"repost_{len(self._reposts) + 1}",
            "source_protocol": source_protocol,
            "target_protocol": target_protocol,
            "original_post_id": original_post_id,
            "original_author": original_author,
            "reposter": reposter,
            "content_length": len(content),
            "content_preview": content[:100],
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "published",
            "target_url": f"https://{target_protocol}.social/repost/{len(self._reposts) + 1}",
        }
        self._reposts.append(repost)

        # Track engagement
        post_key = f"{source_protocol}:{original_post_id}"
        if post_key not in self._engagements:
            self._engagements[post_key] = {}
        self._engagements[post_key][target_protocol] = self._engagements[post_key].get(target_protocol, 0) + 1

        return repost

    def get_reposts_for_post(self, post_id: str, source_protocol: str) -> list[dict[str, Any]]:
        """Get all reposts of a specific post."""
        return [
            r for r in self._reposts if r["original_post_id"] == post_id and r["source_protocol"] == source_protocol
        ]

    def get_repost_count(self, post_id: str, source_protocol: str) -> int:
        """Get total repost count across all target protocols."""
        return len(self.get_reposts_for_post(post_id, source_protocol))

    def get_protocol_reach(self, post_id: str, source_protocol: str) -> dict[str, int]:
        """Get repost distribution per target protocol."""
        reposts = self.get_reposts_for_post(post_id, source_protocol)
        reach: dict[str, int] = {}
        for r in reposts:
            target = r["target_protocol"]
            reach[target] = reach.get(target, 0) + 1
        return reach

    def clear(self) -> None:
        self._reposts.clear()
        self._engagements.clear()


@pytest.fixture
def engine() -> CrossProtocolRepostEngine:
    return CrossProtocolRepostEngine()


# ═════════════════════════════════════════════════════════════════
# Tests
# ═════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestCrossProtocolRepost:
    """Repost content between protocol pairs."""

    def test_repost_from_nostr_to_farcaster(self, engine: CrossProtocolRepostEngine) -> None:
        """A post on Nostr can be reposted to Farcaster."""
        result = engine.repost(
            source_protocol="nostr",
            target_protocol="farcaster",
            original_post_id="nostr_post_1",
            original_author="npub1user...",
            content="Great post about Bitcoin!",
            reposter="0xFarcasterUser",
        )
        assert result["status"] == "published"
        assert result["source_protocol"] == "nostr"
        assert result["target_protocol"] == "farcaster"

    def test_repost_between_all_protocol_pairs(self, engine: CrossProtocolRepostEngine) -> None:
        """Reposting works between all valid protocol pairs."""
        protocols = ["nostr", "farcaster", "bluesky", "lens", "twitter"]
        pairs = [(src, tgt) for src in protocols for tgt in protocols if src != tgt]
        for src, tgt in pairs:
            result = engine.repost(
                source_protocol=src,
                target_protocol=tgt,
                original_post_id=f"{src}_post_1",
                original_author=f"user@{src}",
                content=f"Reposted from {src} to {tgt}!",
                reposter=f"user@{tgt}",
            )
            assert result["status"] == "published"

    def test_repost_from_multiple_sources_to_same_target(self, engine: CrossProtocolRepostEngine) -> None:
        """Content from different protocols can be reposted to the same target."""
        for src in ["nostr", "farcaster", "bluesky"]:
            engine.repost(
                source_protocol=src,
                target_protocol="lens",
                original_post_id=f"{src}_content",
                original_author=f"user@{src}",
                content=f"From {src} to Lens",
                reposter="user@lens",
            )
        lens_reposts = [r for r in engine._reposts if r["target_protocol"] == "lens"]
        assert len(lens_reposts) == 3

    def test_repost_from_single_to_all_protocols(self, engine: CrossProtocolRepostEngine) -> None:
        """A single post can be reposted to all other protocols."""
        targets = ["farcaster", "bluesky", "lens", "twitter"]
        for tgt in targets:
            engine.repost(
                source_protocol="nostr",
                target_protocol=tgt,
                original_post_id="viralmeme_001",
                original_author="npub1creator...",
                content="Viral meme reposting!",
                reposter=f"user@{tgt}",
            )
        count = engine.get_repost_count("viralmeme_001", "nostr")
        assert count == 4

    def test_original_author_preserved_in_repost(self, engine: CrossProtocolRepostEngine) -> None:
        """Original author is preserved when reposting."""
        result = engine.repost(
            source_protocol="farcaster",
            target_protocol="nostr",
            original_post_id="fc_original",
            original_author="0xCreator",
            content="Original content",
            reposter="npub1reposter",
        )
        assert result["original_author"] == "0xCreator"
        assert result["reposter"] == "npub1reposter"

    def test_repost_includes_content_preview(self, engine: CrossProtocolRepostEngine) -> None:
        """Repost includes a preview of the original content."""
        content = "This is a long post about the future of decentralized social media and cross-protocol communication"
        result = engine.repost(
            source_protocol="lens",
            target_protocol="twitter",
            original_post_id="lens_long",
            original_author="user.lens",
            content=content,
            reposter="@twitteruser",
        )
        assert result["content_length"] == len(content)
        assert len(result["content_preview"]) <= 100


@pytest.mark.integration
class TestCrossProtocolRepostValidation:
    """Repost validation rules."""

    def test_rejects_same_protocol_repost(self, engine: CrossProtocolRepostEngine) -> None:
        """Reposting within the same protocol is rejected."""
        with pytest.raises(ValueError, match="must differ"):
            engine.repost(
                source_protocol="nostr",
                target_protocol="nostr",
                original_post_id="p1",
                original_author="user",
                content="test",
                reposter="user2",
            )

    def test_rejects_unknown_source_protocol(self, engine: CrossProtocolRepostEngine) -> None:
        """Unknown source protocol is rejected."""
        with pytest.raises(ValueError, match="Unknown source"):
            engine.repost(
                source_protocol="unknown_proto",
                target_protocol="nostr",
                original_post_id="p1",
                original_author="user",
                content="test",
                reposter="user2",
            )

    def test_rejects_unknown_target_protocol(self, engine: CrossProtocolRepostEngine) -> None:
        """Unknown target protocol is rejected."""
        with pytest.raises(ValueError, match="Unknown target"):
            engine.repost(
                source_protocol="nostr",
                target_protocol="fake_proto",
                original_post_id="p1",
                original_author="user",
                content="test",
                reposter="user2",
            )

    def test_rejects_empty_content(self, engine: CrossProtocolRepostEngine) -> None:
        """Repost with empty content is rejected."""
        with pytest.raises(ValueError, match="cannot be empty"):
            engine.repost(
                source_protocol="nostr",
                target_protocol="farcaster",
                original_post_id="p1",
                original_author="user",
                content="",
                reposter="user2",
            )


@pytest.mark.integration
class TestCrossProtocolRepostTracking:
    """Repost tracking and analytics."""

    def test_tracks_repost_count_per_post(self, engine: CrossProtocolRepostEngine) -> None:
        """Repost count is tracked per post."""
        engine.repost(
            source_protocol="farcaster",
            target_protocol="nostr",
            original_post_id="hot_post",
            original_author="0xFC",
            content="Hot take!",
            reposter="npub1user",
        )
        engine.repost(
            source_protocol="farcaster",
            target_protocol="bluesky",
            original_post_id="hot_post",
            original_author="0xFC",
            content="Hot take on bsky!",
            reposter="bskyuser",
        )
        count = engine.get_repost_count("hot_post", "farcaster")
        assert count == 2

    def test_repost_count_initializes_to_zero(self, engine: CrossProtocolRepostEngine) -> None:
        """Post with no reposts returns count 0."""
        count = engine.get_repost_count("nonexistent", "nostr")
        assert count == 0

    def test_protocol_reach_shows_distribution(self, engine: CrossProtocolRepostEngine) -> None:
        """Protocol reach shows repost distribution."""
        engine.repost(
            source_protocol="nostr",
            target_protocol="farcaster",
            original_post_id="viral",
            original_author="npub1",
            content="Viral!",
            reposter="fcuser",
        )
        engine.repost(
            source_protocol="nostr",
            target_protocol="farcaster",
            original_post_id="viral",
            original_author="npub1",
            content="Viral again!",
            reposter="fcuser2",
        )
        engine.repost(
            source_protocol="nostr",
            target_protocol="bluesky",
            original_post_id="viral",
            original_author="npub1",
            content="On bsky!",
            reposter="bskyuser",
        )
        reach = engine.get_protocol_reach("viral", "nostr")
        assert reach.get("farcaster") == 2
        assert reach.get("bluesky") == 1

    def test_repost_timestamps_are_iso_format(self, engine: CrossProtocolRepostEngine) -> None:
        """Repost timestamps are in ISO 8601 format."""
        result = engine.repost(
            source_protocol="nostr",
            target_protocol="farcaster",
            original_post_id="timed_post",
            original_author="npub1",
            content="Timed!",
            reposter="fcuser",
        )
        assert "T" in result["timestamp"]

    def test_reposts_listed_in_chronological_order(self, engine: CrossProtocolRepostEngine) -> None:
        """Reposts for a post are returned in order."""
        for i in range(3):
            engine.repost(
                source_protocol="nostr",
                target_protocol="farcaster",
                original_post_id="ordered_post",
                original_author="npub1",
                content=f"Repost {i}",
                reposter=f"fcuser_{i}",
            )
        reposts = engine.get_reposts_for_post("ordered_post", "nostr")
        assert len(reposts) == 3
        assert reposts[0]["id"] == "repost_1"
        assert reposts[2]["id"] == "repost_3"
