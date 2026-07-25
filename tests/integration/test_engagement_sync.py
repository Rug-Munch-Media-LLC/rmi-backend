"""Integration tests for cross-protocol engagement synchronization.

Tests that engagement metrics (likes, reposts, comments) are properly
synced across protocol boundaries when content is shared between
protocol identities.

Follows the patterns from test_dm_bridge.py and test_cross_protocol_repost.py.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}


# ── Engagement Sync Engine ────────────────────────────────────────


class EngagementSyncEngine:
    """Syncs engagement metrics (likes, reposts, comments) across protocols."""

    PROTOCOLS = ["nostr", "farcaster", "bluesky", "lens", "twitter"]

    def __init__(self) -> None:
        self._engagements: dict[str, dict[str, int]] = {}  # content_id -> {metric: count}
        self._cross_protocol_activity: list[dict[str, Any]] = []

    def _content_key(self, content_id: str, source_protocol: str) -> str:
        return f"{source_protocol}:{content_id}"

    def record_engagement(
        self,
        content_id: str,
        source_protocol: str,
        metric: str,
        delta: int = 1,
        actor_protocol: str | None = None,
    ) -> dict[str, Any]:
        """Record an engagement event on content from a specific protocol."""
        if source_protocol not in self.PROTOCOLS:
            raise ValueError(f"Unknown source protocol: {source_protocol}")
        if metric not in ("like", "repost", "comment"):
            raise ValueError(f"Unknown metric: {metric}")

        key = self._content_key(content_id, source_protocol)
        if key not in self._engagements:
            self._engagements[key] = {"likes": 0, "reposts": 0, "comments": 0}

        metric_map = {"like": "likes", "repost": "reposts", "comment": "comments"}
        self._engagements[key][metric_map[metric]] += delta

        activity = {
            "content_id": content_id,
            "source_protocol": source_protocol,
            "metric": metric,
            "delta": delta,
            "actor_protocol": actor_protocol or source_protocol,
            "timestamp": datetime.now(UTC).isoformat(),
            "total": self._engagements[key][metric_map[metric]],
        }
        self._cross_protocol_activity.append(activity)
        return activity

    def get_engagement(self, content_id: str, source_protocol: str) -> dict[str, int]:
        """Get total engagement for content from a specific protocol."""
        key = self._content_key(content_id, source_protocol)
        return self._engagements.get(key, {"likes": 0, "reposts": 0, "comments": 0})

    def get_cross_protocol_breakdown(self, content_id: str, source_protocol: str) -> dict[str, dict[str, int]]:
        """Get engagement breakdown by actor protocol."""
        key = self._content_key(content_id, source_protocol)
        breakdown: dict[str, dict[str, int]] = {}
        for activity in self._cross_protocol_activity:
            if activity["content_id"] != content_id:
                continue
            if activity["source_protocol"] != source_protocol:
                continue
            actor = activity["actor_protocol"]
            metric = activity["metric"]
            if actor not in breakdown:
                breakdown[actor] = {"likes": 0, "reposts": 0, "comments": 0}
            metric_map = {"like": "likes", "repost": "reposts", "comment": "comments"}
            breakdown[actor][metric_map[metric]] += activity["delta"]
        return breakdown

    def get_content_reach(self, content_id: str, source_protocol: str) -> dict[str, Any]:
        """Get total reach of content across all protocols."""
        engagement = self.get_engagement(content_id, source_protocol)
        cross_protocol = self.get_cross_protocol_breakdown(content_id, source_protocol)
        protocols_with_activity = len(cross_protocol) - (1 if source_protocol in cross_protocol else 0)

        return {
            "content_id": content_id,
            "source_protocol": source_protocol,
            "total_engagements": sum(engagement.values()),
            "protocols_reached": protocols_with_activity,
            "cross_protocol_pct": ((protocols_with_activity / len(self.PROTOCOLS)) * 100 if self.PROTOCOLS else 0),
        }

    def sync_engagement(
        self, content_id: str, source_protocol: str, target_protocols: list[str]
    ) -> list[dict[str, Any]]:
        """Simulate syncing engagement to target protocol mirrors."""
        source_engagement = self.get_engagement(content_id, source_protocol)
        results: list[dict[str, Any]] = []
        for target in target_protocols:
            if target == source_protocol:
                continue
            if target not in self.PROTOCOLS:
                continue
            results.append(
                {
                    "content_id": content_id,
                    "target_protocol": target,
                    "synced_likes": source_engagement["likes"],
                    "synced_reposts": source_engagement["reposts"],
                    "synced_comments": source_engagement["comments"],
                    "sync_timestamp": datetime.now(UTC).isoformat(),
                    "status": "synced",
                }
            )
        return results

    def clear(self) -> None:
        self._engagements.clear()
        self._cross_protocol_activity.clear()


@pytest.fixture
def sync() -> EngagementSyncEngine:
    return EngagementSyncEngine()


# ═════════════════════════════════════════════════════════════════
# Tests
# ═════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestEngagementRecording:
    """Record engagement events across protocols."""

    def test_record_like_on_nostr_content(self, sync: EngagementSyncEngine) -> None:
        """A like on Nostr content is recorded."""
        activity = sync.record_engagement(
            content_id="nostr_post_1",
            source_protocol="nostr",
            metric="like",
        )
        assert activity["metric"] == "like"
        assert activity["source_protocol"] == "nostr"
        assert activity["total"] == 1

    def test_record_like_from_different_actor_protocol(self, sync: EngagementSyncEngine) -> None:
        """A like on Farcaster content from a Bluesky user is recorded."""
        activity = sync.record_engagement(
            content_id="fc_post_1",
            source_protocol="farcaster",
            metric="like",
            actor_protocol="bluesky",
        )
        assert activity["actor_protocol"] == "bluesky"
        assert activity["source_protocol"] == "farcaster"

    def test_record_all_metric_types(self, sync: EngagementSyncEngine) -> None:
        """Likes, reposts, and comments are all recordable."""
        for metric in ("like", "repost", "comment"):
            activity = sync.record_engagement(
                content_id="post_metrics",
                source_protocol="lens",
                metric=metric,
            )
            assert activity["metric"] == metric

    def test_multiple_engagements_accumulate(self, sync: EngagementSyncEngine) -> None:
        """Multiple engagements on the same content accumulate."""
        for _ in range(5):
            sync.record_engagement(
                content_id="popular_post",
                source_protocol="nostr",
                metric="like",
            )
        engagement = sync.get_engagement("popular_post", "nostr")
        assert engagement["likes"] == 5

    def test_different_metrics_independent(self, sync: EngagementSyncEngine) -> None:
        """Likes, reposts, and comments are tracked independently."""
        sync.record_engagement("post_indep", "farcaster", "like", delta=10)
        sync.record_engagement("post_indep", "farcaster", "repost", delta=3)
        sync.record_engagement("post_indep", "farcaster", "comment", delta=7)

        engagement = sync.get_engagement("post_indep", "farcaster")
        assert engagement["likes"] == 10
        assert engagement["reposts"] == 3
        assert engagement["comments"] == 7

    def test_rejects_unknown_protocol(self, sync: EngagementSyncEngine) -> None:
        """Unknown source protocol raises error."""
        with pytest.raises(ValueError, match="Unknown source"):
            sync.record_engagement("p1", "unknown_proto", "like")

    def test_rejects_unknown_metric(self, sync: EngagementSyncEngine) -> None:
        """Unknown metric raises error."""
        with pytest.raises(ValueError, match="Unknown metric"):
            sync.record_engagement("p1", "nostr", "invalid_metric")


@pytest.mark.integration
class TestEngagementRetrieval:
    """Engagement data retrieval."""

    def test_returns_zero_for_unengaged_content(self, sync: EngagementSyncEngine) -> None:
        """Content with no engagement returns zeros."""
        engagement = sync.get_engagement("nonexistent", "nostr")
        assert engagement == {"likes": 0, "reposts": 0, "comments": 0}

    def test_engagement_is_per_content_and_protocol(self, sync: EngagementSyncEngine) -> None:
        """Engagement is scoped to content_id + source_protocol."""
        sync.record_engagement("post_a", "nostr", "like")
        sync.record_engagement("post_a", "farcaster", "like")

        nostr_eng = sync.get_engagement("post_a", "nostr")
        fc_eng = sync.get_engagement("post_a", "farcaster")
        assert nostr_eng["likes"] == 1
        assert fc_eng["likes"] == 1

    def test_cross_protocol_breakdown(self, sync: EngagementSyncEngine) -> None:
        """Cross-protocol engagement breakdown shows per-protocol activity."""
        sync.record_engagement("post_cross", "nostr", "like", actor_protocol="nostr")
        sync.record_engagement("post_cross", "nostr", "like", actor_protocol="farcaster")
        sync.record_engagement("post_cross", "nostr", "like", actor_protocol="bluesky")
        sync.record_engagement("post_cross", "nostr", "repost", actor_protocol="farcaster")

        breakdown = sync.get_cross_protocol_breakdown("post_cross", "nostr")
        assert breakdown["nostr"]["likes"] == 1
        assert breakdown["farcaster"]["likes"] == 1
        assert breakdown["farcaster"]["reposts"] == 1
        assert breakdown["bluesky"]["likes"] == 1
        assert len(breakdown) == 3  # Three protocols engaged

    def test_record_engagement_with_delta(self, sync: EngagementSyncEngine) -> None:
        """Engagement can be recorded with custom delta."""
        sync.record_engagement("post_delta", "nostr", "like", delta=5)
        engagement = sync.get_engagement("post_delta", "nostr")
        assert engagement["likes"] == 5


@pytest.mark.integration
class TestEngagementReach:
    """Content reach analytics."""

    def test_calculates_content_reach(self, sync: EngagementSyncEngine) -> None:
        """Content reach shows how many protocols engaged."""
        sync.record_engagement("reachable", "nostr", "like", actor_protocol="nostr")
        sync.record_engagement("reachable", "nostr", "like", actor_protocol="farcaster")
        sync.record_engagement("reachable", "nostr", "repost", actor_protocol="bluesky")

        reach = sync.get_content_reach("reachable", "nostr")
        assert reach["total_engagements"] == 3
        assert reach["protocols_reached"] >= 2  # nostr + farcaster/bluesky

    def test_reach_with_no_cross_protocol_activity(self, sync: EngagementSyncEngine) -> None:
        """Content with only same-protocol activity has 0 cross reach."""
        sync.record_engagement("isolated", "nostr", "like", actor_protocol="nostr")
        reach = sync.get_content_reach("isolated", "nostr")
        assert reach["protocols_reached"] == 0

    def test_reach_percentage_calculation(self, sync: EngagementSyncEngine) -> None:
        """Reach percentage is calculated correctly."""
        sync.record_engagement("wide", "nostr", "like", actor_protocol="farcaster")
        sync.record_engagement("wide", "nostr", "like", actor_protocol="bluesky")
        sync.record_engagement("wide", "nostr", "like", actor_protocol="lens")

        reach = sync.get_content_reach("wide", "nostr")
        assert reach["cross_protocol_pct"] > 0
        assert reach["cross_protocol_pct"] <= 100


@pytest.mark.integration
class TestEngagementSync:
    """Syncing engagement across protocol mirrors."""

    def test_sync_engagement_to_target(self, sync: EngagementSyncEngine) -> None:
        """Engagement syncs from source to target protocol."""
        sync.record_engagement("sync_me", "nostr", "like", delta=10)
        sync.record_engagement("sync_me", "nostr", "repost", delta=5)

        results = sync.sync_engagement("sync_me", "nostr", ["farcaster"])
        assert len(results) == 1
        assert results[0]["target_protocol"] == "farcaster"
        assert results[0]["synced_likes"] == 10
        assert results[0]["synced_reposts"] == 5

    def test_sync_to_multiple_targets(self, sync: EngagementSyncEngine) -> None:
        """Engagement syncs to multiple target protocols."""
        sync.record_engagement("multi_sync", "farcaster", "like", delta=7)
        sync.record_engagement("multi_sync", "farcaster", "comment", delta=3)

        results = sync.sync_engagement("multi_sync", "farcaster", ["nostr", "bluesky", "lens"])
        assert len(results) == 3
        for r in results:
            assert r["synced_likes"] == 7
            assert r["synced_comments"] == 3
            assert r["status"] == "synced"

    def test_sync_skips_same_protocol(self, sync: EngagementSyncEngine) -> None:
        """Syncing to the same protocol as source is skipped."""
        results = sync.sync_engagement("p1", "nostr", ["nostr", "farcaster"])
        target_protocols = [r["target_protocol"] for r in results]
        assert "nostr" not in target_protocols

    def test_sync_skips_unknown_protocols(self, sync: EngagementSyncEngine) -> None:
        """Syncing to unknown protocols is skipped."""
        results = sync.sync_engagement("p1", "nostr", ["farcaster", "unknown"])
        assert len(results) == 1
        assert results[0]["target_protocol"] == "farcaster"

    def test_sync_includes_timestamp(self, sync: EngagementSyncEngine) -> None:
        """Sync results include ISO timestamp."""
        results = sync.sync_engagement("p1", "nostr", ["farcaster"])
        assert "T" in results[0]["sync_timestamp"]


@pytest.mark.integration
class TestEngagementEdgeCases:
    """Edge cases for engagement tracking."""

    def test_engagement_timestamps_are_iso_format(self, sync: EngagementSyncEngine) -> None:
        """Engagement activity timestamps are ISO 8601."""
        activity = sync.record_engagement("timed", "nostr", "like")
        assert "T" in activity["timestamp"]

    def test_tracks_actor_protocol_separately(self, sync: EngagementSyncEngine) -> None:
        """Actor protocol is tracked separately from source protocol."""
        activity = sync.record_engagement("cross_actor", "nostr", "like", actor_protocol="farcaster")
        assert activity["actor_protocol"] == "farcaster"
        assert activity["source_protocol"] == "nostr"

    def test_clear_resets_all_data(self, sync: EngagementSyncEngine) -> None:
        """Clearing the engine removes all data."""
        sync.record_engagement("p1", "nostr", "like")
        assert len(sync._cross_protocol_activity) > 0
        sync.clear()
        assert len(sync._cross_protocol_activity) == 0
        assert sync.get_engagement("p1", "nostr") == {"likes": 0, "reposts": 0, "comments": 0}

    def test_empty_target_list_returns_empty(self, sync: EngagementSyncEngine) -> None:
        """Syncing to an empty target list returns empty results."""
        results = sync.sync_engagement("p1", "nostr", [])
        assert results == []
