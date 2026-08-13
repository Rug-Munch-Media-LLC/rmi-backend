"""Unit tests for news story clustering (persistent keys + long window)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domains.news.clusterer import (
    NewsItem,
    StoryCluster,
    _bias_for_source,
    _persistent_cluster_key,
    cluster_items,
)


def _item(
    idx: str,
    title: str,
    source: str,
    published_at: datetime,
    body: str = "",
) -> NewsItem:
    return NewsItem(
        id=idx,
        title=title,
        body=body,
        source=source,
        url=f"https://example.com/{idx}",
        published_at=published_at,
        sentiment=0.0,
    )


def test_persistent_key_stable_across_runs() -> None:
    """The same story maps to the same cluster_id on re-clustering."""
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    items = [
        _item("a1", "Bitcoin ETF sees record inflows as institutions pile in", "CoinDesk", t0),
        _item("a2", "Bitcoin ETF inflows hit record as institutions pile in", "The Block", t0 + timedelta(minutes=5)),
    ]
    s1 = cluster_items(items, window_minutes=4320)[0]
    # Re-cluster with the same items but different ids — the persistent key
    # derives from content (MinHash signature), not member ids.
    items2 = [
        _item("x1", "Bitcoin ETF sees record inflows as institutions pile in", "CoinDesk", t0),
        _item("x2", "Bitcoin ETF inflows hit record as institutions pile in", "The Block", t0 + timedelta(minutes=5)),
    ]
    s2 = cluster_items(items2, window_minutes=4320)[0]
    assert s1.cluster_id == s2.cluster_id


def test_multi_day_story_clusters_with_long_window() -> None:
    """Two items 48h apart cluster with a 72h window but not a 30-min one."""
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    items = [
        _item("d1", "Ethereum Shanghai upgrade goes live on mainnet", "CoinDesk", t0),
        _item(
            "d2",
            "Ethereum Shanghai upgrade goes live on mainnet",  # same story, 48h later
            "The Block",
            t0 + timedelta(hours=48),
        ),
    ]
    long_window = cluster_items(items, window_minutes=4320)  # 72h
    assert len(long_window) == 1
    assert long_window[0].item_count == 2

    short_window = cluster_items(items, window_minutes=30)  # legacy behavior
    assert len(short_window) == 2


def test_source_count_and_bias_distribution() -> None:
    """A multi-source story reports distinct source count + bias spread."""
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    items = [
        _item("b1", "Solana breaks out to new high on ETF speculation", "CoinDesk", t0),
        _item("b2", "Solana breaks out to new high on ETF speculation", "The Block", t0 + timedelta(minutes=5)),
        _item("b3", "Solana breaks out to new high on ETF speculation", "Reddit", t0 + timedelta(minutes=10)),
    ]
    stories = cluster_items(items, window_minutes=4320)
    assert len(stories) == 1
    story = stories[0]
    assert story.source_count == 3
    assert story.sources == ["CoinDesk", "Reddit", "The Block"]
    assert story.bias_distribution == {"center": 3}  # all three are center bias
    assert story.item_count == 3


def test_unrelated_items_stay_separate() -> None:
    """Similar-looking headlines about different topics must not merge."""
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    items = [
        _item("u1", "Bitcoin price drops after Fed minutes", "CoinDesk", t0),
        _item("u2", "Ethereum gas fees hit yearly low", "The Block", t0 + timedelta(minutes=5)),
    ]
    stories = cluster_items(items, window_minutes=4320)
    assert len(stories) == 2


def test_bias_for_source_known_and_unknown() -> None:
    assert _bias_for_source("CoinDesk") == "center"
    assert _bias_for_source("reuters") == "center"
    assert _bias_for_source("totally-unknown-outlet") == "center"


def test_story_cluster_to_dict_includes_dedup_fields() -> None:
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    story = StoryCluster(
        cluster_id="abc",
        representative_title="Title",
        source_urls=["https://a"],
        sources=["A"],
        first_seen=t0,
        last_updated=t0,
        item_count=1,
        sentiment_avg=0.0,
        item_ids=["i1"],
        source_count=1,
        bias_distribution={"center": 1},
    )
    d = story.to_dict()
    assert d["source_count"] == 1
    assert d["bias_distribution"] == {"center": 1}
    assert d["cluster_id"] == "abc"


def test_persistent_key_is_content_derived() -> None:
    """Key length/format sanity check."""
    key = _persistent_cluster_key([1, 2, 3, 4, 5] * 20)
    assert len(key) == 16
    assert _persistent_cluster_key([1, 2, 3, 4, 5] * 20) == key
