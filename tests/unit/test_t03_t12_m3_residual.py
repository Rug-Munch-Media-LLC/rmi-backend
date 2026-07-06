"""Unit tests for T03 (news clusterer) and T12 (CertStream match_brand)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.news.clusterer import NewsItem, cluster_items
from app.domain.threat.certstream_listener import match_brand


# ── T03: clusterer tests ───────────────────────────────────────────
def test_clusterer_groups_similar_stories():
    """Two items with same/similar story should cluster together."""
    base = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
    items = [
        NewsItem(
            id="a1",
            title="Bitcoin hits new all-time high above 120000",
            body="BTC surged past 120000 today as ETF inflows hit record",
            source="coindesk", url="https://coindesk.com/1", published_at=base,
        ),
        NewsItem(
            id="a2",
            title="Bitcoin hits new all-time high above 120000",
            body="BTC surged past 120000 today as ETF inflows hit record",
            source="the block", url="https://theblock.co/2", published_at=base + timedelta(minutes=5),
        ),
        NewsItem(
            id="b1",
            title="Ethereum upgrade scheduled for next month",
            body="Core developers announce Pectra hard fork for July",
            source="decrypt", url="https://decrypt.co/3", published_at=base + timedelta(minutes=2),
        ),
    ]
    stories = cluster_items(items)
    # Should produce 2 stories (2 BTC items clustered + 1 ETH item singleton)
    assert len(stories) == 2, f"expected 2 stories, got {len(stories)}"
    btc_story = next(s for s in stories if s.item_count == 2)
    assert btc_story.item_count == 2
    assert "coindesk" in btc_story.sources
    assert "the block" in btc_story.sources
    assert len(btc_story.item_ids) == 2


def test_clusterer_handles_singleton():
    """Single item → single story (singleton)."""
    base = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
    items = [
        NewsItem(
            id="x1", title="Unique story nobody else is covering",
            body="Something happened once",
            source="reddit", url="https://reddit.com/x", published_at=base,
        ),
    ]
    stories = cluster_items(items)
    assert len(stories) == 1
    assert stories[0].item_count == 1


def test_clusterer_respects_time_window():
    """Items in different time windows should not cluster together."""
    base = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
    items = [
        NewsItem(
            id="m1", title="Bitcoin hits new high",
            body="BTC surged past $120K",
            source="coindesk", url="", published_at=base,
        ),
        NewsItem(
            id="m2", title="Bitcoin hits new high",
            body="BTC surged past $120K",
            source="the block", url="", published_at=base + timedelta(hours=2),
        ),
    ]
    # With 30-min windows, these are in separate buckets → 2 singleton stories
    stories = cluster_items(items, window_minutes=30)
    assert len(stories) == 2


def test_clusterer_empty():
    assert cluster_items([]) == []


# ── T12: match_brand tests ─────────────────────────────────────────
def test_match_brand_flags_phishing_clone():
    """'metamask-secure-claim.com' should flag as phishing of 'metamask'."""
    brand = match_brand("metamask-secure-claim.com")
    assert brand == "metamask"


def test_match_brand_passes_official_domain():
    """'metamask.io' should NOT be flagged (it's the official domain)."""
    brand = match_brand("metamask.io")
    assert brand is None


def test_match_brand_flags_subdomain_phish():
    """'login-ledger.com' should flag as 'ledger' phishing."""
    brand = match_brand("login-ledger.com")
    assert brand == "ledger"


def test_match_brand_ignores_unrelated():
    brand = match_brand("some-random-website.com")
    assert brand is None


def test_match_brand_strips_wildcard():
    """CertStream sometimes gives '*.example.com' - strip the wildcard."""
    assert match_brand("*.metamask-secure.com") == "metamask"


def test_match_brand_handles_empty():
    assert match_brand("") is None
    assert match_brand(".") is None
