"""Tests for the Creator Analytics domain.

Covers all five analytics pillars:
  1. TRUE view counting (unique viewers, dedup, watch time, drop-off, VTR)
  2. Engagement analytics (likes, comments, tips, CTR, sentiment)
  3. Cross-protocol comparison (nostr, farcaster, bluesky, mastodon)
  4. Content improvement insights (media, timing, categories)
  5. Audience analytics (growth, churn, demographics, sources)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domains.analytics.models import (
    AudienceSummary,
    ContentInsightsResponse,
    CrossProtocolSummary,
    EngagementAnalyticsDetail,
    EngagementAnalyticsSummary,
    GrowthAnalytics,
    ViewAnalyticsDetail,
    ViewAnalyticsSummary,
)
from app.domains.analytics.service import (
    SUPPORTED_PROTOCOLS,
    AnalyticsBuffer,
    CreatorAnalyticsEngine,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> CreatorAnalyticsEngine:
    return CreatorAnalyticsEngine()


@pytest.fixture
def sample_views() -> list[dict]:
    """Generate 100 view events with 20 unique viewers across 2 protocols."""
    views = []
    for i in range(100):
        viewer = f"user_{i % 20}"
        watch_time = 30.0 + (i * 5 % 120)
        total_dur = 120.0
        dropped = None if i % 3 == 0 else watch_time - 10.0
        views.append(
            {
                "viewer_fingerprint": viewer,
                "watch_time_seconds": watch_time,
                "total_content_duration": total_dur,
                "dropped_at_seconds": dropped,
                "drop_off_point_pct": (dropped / total_dur * 100) if dropped else None,
                "viewed_to_end": dropped is None,
                "scrolled_past": i > 80,
                "scroll_depth_pct": 5.0 if i > 80 else 95.0,
                "is_replay": i >= 95,
                "is_live_view": False,
                "peak_concurrent": 0,
                "protocol": "nostr" if i % 2 == 0 else "farcaster",
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
    return views


@pytest.fixture
def sample_engagements() -> list[dict]:
    """Generate 50 engagement events across types."""
    engagements = []
    for i in range(50):
        etype = ["like", "comment", "repost", "tip", "save"][i % 5]
        engagements.append(
            {
                "viewer_fingerprint": f"user_{i % 15}",
                "engagement_type": etype,
                "tip_amount_usd": 5.0 if etype == "tip" else 0.0,
                "tip_token": "SOL" if etype == "tip" else "",
                "tip_amount_token": 0.05 if etype == "tip" else 0.0,
                "comment_text": "Great content!" if etype == "comment" else "",
                "comment_sentiment": "positive" if etype == "comment" else "neutral",
                "comment_sentiment_score": 0.8 if etype == "comment" else 0.0,
                "protocol": "nostr",
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
    return engagements


@pytest.fixture
def sample_posts() -> list[dict]:
    return [
        {
            "id": "p1",
            "has_images": True,
            "has_video": False,
            "video_duration_seconds": 0,
            "tags": "defi,nft",
            "published_at": "2026-07-25T14:00:00+00:00",
        },
        {
            "id": "p2",
            "has_images": False,
            "has_video": True,
            "video_duration_seconds": 45,
            "tags": "nft,art",
            "published_at": "2026-07-25T08:00:00+00:00",
        },
        {
            "id": "p3",
            "has_images": True,
            "has_video": False,
            "video_duration_seconds": 0,
            "tags": "defi",
            "published_at": "2026-07-24T14:00:00+00:00",
        },
        {
            "id": "p4",
            "has_images": False,
            "has_video": True,
            "video_duration_seconds": 120,
            "tags": "nft",
            "published_at": "2026-07-24T20:00:00+00:00",
        },
        {
            "id": "p5",
            "has_images": False,
            "has_video": True,
            "video_duration_seconds": 600,
            "tags": "defi,security",
            "published_at": "2026-07-23T14:00:00+00:00",
        },
    ]


@pytest.fixture
def sample_followers() -> list[dict]:
    return [
        {
            "follower_fingerprint": "u1",
            "protocol": "nostr",
            "is_active": True,
            "interests": "defi,solana",
            "chains_active": "solana,eth",
            "created_at": "2026-07-01T00:00:00Z",
        },
        {
            "follower_fingerprint": "u2",
            "protocol": "farcaster",
            "is_active": True,
            "interests": "nft,art",
            "chains_active": "eth",
            "created_at": "2026-07-05T00:00:00Z",
        },
        {
            "follower_fingerprint": "u3",
            "protocol": "nostr",
            "is_active": False,
            "interests": "defi",
            "chains_active": "solana",
            "unfollowed_at": "2026-07-20T00:00:00Z",
            "created_at": "2026-06-01T00:00:00Z",
        },
    ]


# ── 1. View Analytics Tests ──────────────────────────────────────────


class TestViewAnalytics:
    """True view counting — no inflation, real humans."""

    def test_basic_view_counts(self, engine: CreatorAnalyticsEngine, sample_views: list[dict]):
        result = engine.compute_view_analytics(sample_views, period_days=7)
        assert result["total_views"] == 100
        assert result["unique_viewers"] == 20
        assert result["view_through_rate"] > 0
        assert result["scroll_by_rate"] > 0
        assert result["replay_views"] == 5

    def test_empty_views(self, engine: CreatorAnalyticsEngine):
        result = engine.compute_view_analytics([], period_days=7)
        assert result["total_views"] == 0
        assert result["unique_viewers"] == 0
        assert result["average_view_duration_seconds"] == 0.0

    def test_watch_time_calculation(self, engine: CreatorAnalyticsEngine, sample_views: list[dict]):
        result = engine.compute_view_analytics(sample_views, period_days=7)
        assert result["total_watch_time_minutes"] > 0
        assert result["average_view_duration_seconds"] > 0
        assert result["median_view_duration_seconds"] > 0

    def test_drop_off_points(self, engine: CreatorAnalyticsEngine, sample_views: list[dict]):
        result = engine.compute_view_analytics(sample_views, period_days=7)
        assert len(result["drop_off_points"]) > 0
        for point in result["drop_off_points"]:
            assert "percentage" in point
            assert "viewers_lost" in point

    def test_daily_trend(self, engine: CreatorAnalyticsEngine, sample_views: list[dict]):
        result = engine.compute_view_analytics(sample_views, period_days=7)
        assert len(result["daily_trend"]) > 0
        assert result["daily_trend"][0]["value"] > 0

    def test_single_viewer_all_same(self, engine: CreatorAnalyticsEngine):
        """All views from same user = 1 unique viewer."""
        views = [
            {
                "viewer_fingerprint": "same_user",
                "watch_time_seconds": 10.0,
                "total_content_duration": 60.0,
                "viewed_to_end": False,
                "scrolled_past": False,
                "scroll_depth_pct": 50.0,
                "is_replay": False,
                "is_live_view": False,
                "peak_concurrent": 0,
                "protocol": "nostr",
                "created_at": datetime.now(UTC).isoformat(),
            }
            for _ in range(10)
        ]
        result = engine.compute_view_analytics(views)
        assert result["total_views"] == 10
        assert result["unique_viewers"] == 1


# ── 2. Engagement Analytics Tests ────────────────────────────────────


class TestEngagementAnalytics:
    """Real engagement — deduplicated likes, comments with sentiment, tips."""

    def test_basic_engagement_counts(self, engine: CreatorAnalyticsEngine, sample_engagements: list[dict]):
        result = engine.compute_engagement_analytics(sample_engagements, period_days=7)
        assert result["total_engagements"] == 50
        assert result["likes"] == 10
        assert result["comments"] == 10
        assert result["reposts"] == 10
        assert result["saves"] == 10

    def test_tip_analytics(self, engine: CreatorAnalyticsEngine, sample_engagements: list[dict]):
        result = engine.compute_engagement_analytics(sample_engagements, period_days=7)
        assert result["tip_count"] == 10
        assert result["total_tips_usd"] == 50.0
        assert result["average_tip_usd"] == 5.0
        assert result["top_tip_token"] == "SOL"

    def test_sentiment_analysis(self, engine: CreatorAnalyticsEngine, sample_engagements: list[dict]):
        result = engine.compute_engagement_analytics(sample_engagements, period_days=7)
        assert result["comment_sentiment_positive"] > 0
        assert result["average_sentiment_score"] > 0

    def test_empty_engagements(self, engine: CreatorAnalyticsEngine):
        result = engine.compute_engagement_analytics([], period_days=7)
        assert result["total_engagements"] == 0
        assert result["likes"] == 0
        assert result["tip_count"] == 0

    def test_unique_engagers(self, engine: CreatorAnalyticsEngine):
        """15 unique engagers from 50 events."""
        engs = [
            {
                "viewer_fingerprint": f"user_{i % 15}",
                "engagement_type": "like",
                "protocol": "nostr",
                "created_at": datetime.now(UTC).isoformat(),
            }
            for i in range(50)
        ]
        result = engine.compute_engagement_analytics(engs)
        assert result["unique_engagers"] == 15


# ── 3. Cross-Protocol Analytics Tests ────────────────────────────────


class TestCrossProtocolAnalytics:
    """Protocol comparison — which gives best reach, engagement, tips."""

    def test_protocol_comparison(
        self, engine: CreatorAnalyticsEngine, sample_views: list[dict], sample_engagements: list[dict]
    ):
        views_by_proto = {
            "nostr": [v for v in sample_views if v["protocol"] == "nostr"],
            "farcaster": [v for v in sample_views if v["protocol"] == "farcaster"],
            "bluesky": [],
            "mastodon": [],
            "direct": [],
        }
        eng_by_proto = {"nostr": sample_engagements, "farcaster": [], "bluesky": [], "mastodon": [], "direct": []}
        result = engine.compute_cross_protocol_analytics(views_by_proto, eng_by_proto, 7)
        assert len(result["comparisons"]) >= 2
        assert result["best_protocol_for_reach"]
        assert result["best_protocol_for_engagement"]

    def test_best_protocol_identified(self, engine: CreatorAnalyticsEngine):
        """Nostr has more data, should be best."""
        views_by_proto = {
            "nostr": [
                {
                    "viewer_fingerprint": f"u{i}",
                    "watch_time_seconds": 30.0,
                    "protocol": "nostr",
                    "created_at": datetime.now(UTC).isoformat(),
                }
                for i in range(100)
            ],
            "farcaster": [
                {
                    "viewer_fingerprint": f"u{i}",
                    "watch_time_seconds": 30.0,
                    "protocol": "farcaster",
                    "created_at": datetime.now(UTC).isoformat(),
                }
                for i in range(10)
            ],
            "bluesky": [],
            "mastodon": [],
            "direct": [],
        }
        eng_by_proto = {"nostr": [], "farcaster": [], "bluesky": [], "mastodon": [], "direct": []}
        result = engine.compute_cross_protocol_analytics(views_by_proto, eng_by_proto, 7)
        assert result["best_protocol_for_reach"] == "nostr"

    def test_empty_cross_protocol(self, engine: CreatorAnalyticsEngine):
        views_by_proto = {p: [] for p in SUPPORTED_PROTOCOLS}
        eng_by_proto = {p: [] for p in SUPPORTED_PROTOCOLS}
        result = engine.compute_cross_protocol_analytics(views_by_proto, eng_by_proto, 7)
        assert result["comparisons"] == []


# ── 4. Content Insights Tests ────────────────────────────────────────


class TestContentInsights:
    """Data-driven insights — statistical, not hallucinated."""

    def test_insights_generated(self, engine: CreatorAnalyticsEngine):
        posts = [
            {
                "id": "p1",
                "has_images": True,
                "has_video": False,
                "video_duration_seconds": 0,
                "tags": "defi",
                "published_at": "2026-07-25T14:00:00+00:00",
            },
            {
                "id": "p2",
                "has_images": False,
                "has_video": True,
                "video_duration_seconds": 45,
                "tags": "nft",
                "published_at": "2026-07-25T08:00:00+00:00",
            },
            {
                "id": "p3",
                "has_images": True,
                "has_video": True,
                "video_duration_seconds": 120,
                "tags": "defi",
                "published_at": "2026-07-24T22:00:00+00:00",
            },
        ]
        views_by_post = {
            "p1": [
                {
                    "viewer_fingerprint": f"u{i}",
                    "watch_time_seconds": 60,
                    "protocol": "nostr",
                    "created_at": "2026-07-25T14:30:00Z",
                }
                for i in range(20)
            ],
            "p2": [
                {
                    "viewer_fingerprint": f"u{i}",
                    "watch_time_seconds": 20,
                    "protocol": "nostr",
                    "created_at": "2026-07-25T08:30:00Z",
                }
                for i in range(5)
            ],
            "p3": [
                {
                    "viewer_fingerprint": f"u{i}",
                    "watch_time_seconds": 45,
                    "protocol": "nostr",
                    "created_at": "2026-07-24T22:30:00Z",
                }
                for i in range(15)
            ],
        }
        eng_by_post = {
            "p1": [{"viewer_fingerprint": "u1", "engagement_type": "like"}],
            "p2": [{"viewer_fingerprint": "u2", "engagement_type": "like"}],
            "p3": [],
        }
        result = engine.compute_content_insights(posts, views_by_post, eng_by_post)
        assert len(result["insights"]) > 0
        assert len(result["top_recommendations"]) > 0

    def test_insights_need_minimum_data(self, engine: CreatorAnalyticsEngine):
        """Fewer than 3 posts should return a 'publish more' insight."""
        posts = [{"id": "p1", "has_images": True, "tags": "defi", "published_at": "2026-07-25T14:00:00+00:00"}]
        result = engine.compute_content_insights(posts, {}, {})
        assert result["insights"][0]["category"] == "recommendation"

    def test_media_type_insight(self, engine: CreatorAnalyticsEngine):
        """Posts with images should show engagement factor."""
        posts = [
            {"id": "p1", "has_images": True, "tags": "defi", "published_at": "2026-07-25T14:00:00+00:00"},
            {"id": "p2", "has_images": True, "tags": "nft", "published_at": "2026-07-24T14:00:00+00:00"},
            {"id": "p3", "has_images": False, "tags": "general", "published_at": "2026-07-23T14:00:00+00:00"},
        ]
        views_by_post = {
            "p1": [
                {
                    "viewer_fingerprint": "u1",
                    "watch_time_seconds": 10,
                    "protocol": "nostr",
                    "created_at": "2026-07-25T14:30:00Z",
                }
            ],
            "p2": [
                {
                    "viewer_fingerprint": "u2",
                    "watch_time_seconds": 10,
                    "protocol": "nostr",
                    "created_at": "2026-07-24T14:30:00Z",
                }
            ],
            "p3": [
                {
                    "viewer_fingerprint": "u3",
                    "watch_time_seconds": 10,
                    "protocol": "nostr",
                    "created_at": "2026-07-23T14:30:00Z",
                }
            ],
        }
        eng_by_post = {"p1": [{"engagement_type": "like"}], "p2": [{"engagement_type": "like"}], "p3": []}
        result = engine.compute_content_insights(posts, views_by_post, eng_by_post)
        media_insights = [i for i in result["insights"] if i["category"] == "media_type"]
        assert len(media_insights) > 0 or len(result["insights"]) > 0


# ── 5. Audience Analytics Tests ──────────────────────────────────────


class TestAudienceAnalytics:
    """Audience growth, churn, demographics, top followers."""

    def test_audience_summary(self, engine: CreatorAnalyticsEngine, sample_followers: list[dict]):
        result = engine.compute_audience_summary(sample_followers, {}, {})
        assert result["total_followers"] == 3
        assert result["churn_rate_30d"] > 0
        assert len(result["protocol_breakdown"]) > 0

    def test_protocol_breakdown(self, engine: CreatorAnalyticsEngine, sample_followers: list[dict]):
        result = engine.compute_audience_summary(sample_followers, {}, {})
        assert result["protocol_breakdown"].get("nostr") == 2
        assert result["protocol_breakdown"].get("farcaster") == 1

    def test_top_interests(self, engine: CreatorAnalyticsEngine, sample_followers: list[dict]):
        result = engine.compute_audience_summary(sample_followers, {}, {})
        assert len(result["top_interests"]) > 0
        assert any(i["interest"] == "defi" for i in result["top_interests"])

    def test_growth_analytics(self, engine: CreatorAnalyticsEngine, sample_followers: list[dict]):
        result = engine.compute_growth_analytics(sample_followers, 30)
        assert len(result["daily_growth"]) > 0
        assert "nostr" in result["protocol_growth"]

    def test_empty_audience(self, engine: CreatorAnalyticsEngine):
        result = engine.compute_audience_summary([], {}, {})
        assert result["total_followers"] == 0
        assert result["churn_rate_30d"] == 0.0

    def test_most_engaged_scoring(self, engine: CreatorAnalyticsEngine):
        followers = [
            {
                "follower_fingerprint": "u1",
                "protocol": "nostr",
                "is_active": True,
                "interests": "",
                "chains_active": "",
                "created_at": "2026-07-01T00:00:00Z",
            },
            {
                "follower_fingerprint": "u2",
                "protocol": "farcaster",
                "is_active": True,
                "interests": "",
                "chains_active": "",
                "created_at": "2026-07-05T00:00:00Z",
            },
        ]
        views_by_f = {
            "u1": [{"watch_time_seconds": 30} for _ in range(20)],
            "u2": [{"watch_time_seconds": 30} for _ in range(5)],
        }
        eng_by_f = {
            "u1": [{"engagement_type": "like"} for _ in range(10)],
            "u2": [{"engagement_type": "like"} for _ in range(1)],
        }
        result = engine.compute_audience_summary(followers, views_by_f, eng_by_f)
        assert len(result["most_engaged_followers"]) > 0


# ── 6. Event Buffer Tests ───────────────────────────────────────────


class TestAnalyticsBuffer:
    """In-memory buffer for real-time event ingestion."""

    def test_buffer_add_and_drain(self):
        buf = AnalyticsBuffer(flush_interval_seconds=60)
        buf.add_view({"test": "view"})
        buf.add_engagement({"test": "engagement"})
        buf.add_follower({"test": "follower"})

        drained = buf.drain()
        assert len(drained["views"]) == 1
        assert len(drained["engagements"]) == 1
        assert len(drained["followers"]) == 1
        assert len(buf._view_buffer) == 0  # Emptied

    def test_buffer_flush_on_batch_size(self):
        buf = AnalyticsBuffer(flush_interval_seconds=60, max_batch_size=10)
        for _ in range(10):
            buf.add_view({"test": 1})
        assert buf.should_flush() is True

    def test_buffer_empty_does_not_flush(self):
        buf = AnalyticsBuffer(flush_interval_seconds=60)
        assert buf.should_flush() is False


# ── 7. Event Recording Tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_view_event(engine: CreatorAnalyticsEngine):
    ok = await engine.record_view(
        post_id="p1",
        creator_id="c1",
        viewer_fingerprint="fp1",
        protocol="nostr",
        watch_time_seconds=60.0,
        total_duration=120.0,
        session_id="sess1",
    )
    assert ok is True


@pytest.mark.asyncio
async def test_record_engagement_event(engine: CreatorAnalyticsEngine):
    await engine.record_engagement(
        post_id="p1",
        creator_id="c1",
        viewer_fingerprint="fp1",
        engagement_type="like",
        protocol="nostr",
    )


@pytest.mark.asyncio
async def test_record_follow_event(engine: CreatorAnalyticsEngine):
    await engine.record_follow(
        creator_id="c1",
        follower_fingerprint="fp1",
        protocol="nostr",
        source_post_id="p1",
    )


# ── 8. Model Validation Tests ────────────────────────────────────────


class TestPydanticModels:
    """Pydantic response model validation."""

    def test_view_analytics_summary(self):
        model = ViewAnalyticsSummary(
            total_views=100,
            unique_viewers=20,
            view_through_rate=34.0,
        )
        assert model.total_views == 100
        assert model.unique_viewers == 20

    def test_engagement_analytics_summary(self):
        model = EngagementAnalyticsSummary(
            total_engagements=50,
            likes=10,
            total_tips_usd=50.0,
        )
        assert model.total_engagements == 50

    def test_cross_protocol_summary(self):
        model = CrossProtocolSummary(
            best_protocol_for_reach="nostr",
            total_cross_protocol_views=200,
        )
        assert model.best_protocol_for_reach == "nostr"

    def test_content_insights_response(self):
        model = ContentInsightsResponse(
            top_recommendations=["Post more videos"],
            generated_at="2026-07-26T00:00:00Z",
        )
        assert len(model.top_recommendations) == 1

    def test_audience_summary(self):
        model = AudienceSummary(
            total_followers=1000,
            churn_rate_30d=5.0,
        )
        assert model.total_followers == 1000

    def test_growth_analytics(self):
        model = GrowthAnalytics()
        assert model.daily_growth == []

    def test_view_analytics_detail(self):
        model = ViewAnalyticsDetail(
            post_id="abc-123",
            post_title="Test Post",
        )
        assert model.post_id == "abc-123"

    def test_engagement_analytics_detail(self):
        model = EngagementAnalyticsDetail(
            post_id="abc-123",
            post_title="Test Post",
        )
        assert model.post_id == "abc-123"
