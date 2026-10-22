"""Unit tests for the transparent news-feed ranking (no DB, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domains.news.ranking import (
    collapse_duplicates,
    engagement_multiplier,
    news_item_payload,
    rank_items,
    recency_decay,
    score_row,
    title_key,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def _row(**over: object) -> dict:
    base = {
        "news_id": "n1",
        "title": "Bitcoin ETF inflows hit record",
        "url": "https://a.example/story",
        "source": "CoinDesk",
        "source_trust": 0.9,
        "category": "market",
        "published_at": NOW - timedelta(hours=1),
        "upvotes": 0,
        "downvotes": 0,
        "comment_count": 0,
    }
    base.update(over)
    return base


def test_recency_decay_halves_at_half_life() -> None:
    assert recency_decay(0) == 1.0
    assert recency_decay(8) == 0.5
    assert recency_decay(16) == 0.25
    assert recency_decay(-5) == 1.0  # future-dated clamps


def test_score_is_product_of_logged_components() -> None:
    score, parts = score_row(_row(upvotes=10, comment_count=2), NOW)
    expected = (
        parts["recency_decay"]
        * parts["source_trust"]
        * parts["category_interest"]
        * parts["engagement"]
    )
    assert abs(score - round(expected, 6)) < 1e-6
    assert parts["category_interest"] == 1.0  # "market"
    assert parts["engagement"] > 1.0


def test_engagement_is_capped_and_zero_safe() -> None:
    assert engagement_multiplier() == 1.0
    assert engagement_multiplier(1000, 0, 500) <= 1.6
    assert engagement_multiplier(0, 5, 0) == 1.0  # net negative floors at 0


def test_title_key_ignores_html_case_and_punctuation() -> None:
    assert title_key("<p>Bitcoin: ETF inflows!</p>") == title_key("bitcoin etf inflows")


def test_collapse_keeps_most_trusted_and_lists_others() -> None:
    rows = [
        _row(news_id="a", source="CryptoDaily", source_trust=0.3, url="https://c.example/1"),
        _row(news_id="b", source="CoinDesk", source_trust=0.95, url="https://a.example/1"),
        _row(news_id="c", source="BeInCrypto", source_trust=0.62, url="https://b.example/1"),
    ]
    out = rank_items(rows, NOW)
    assert len(out) == 1
    assert out[0]["news_id"] == "b"
    assert out[0]["source_count"] == 3
    assert {x["source"] for x in out[0]["also_covered_by"]} == {"CryptoDaily", "BeInCrypto"}


def test_blank_titles_are_never_feed_stories() -> None:
    rows = [_row(news_id="x", title=""), _row(news_id="y", title="   ")]
    assert collapse_duplicates(rows) == []


def test_rank_is_monotone_in_recency_and_trust() -> None:
    fresh = _row(news_id="fresh", source="s1", title="Fresh story", published_at=NOW - timedelta(hours=1))
    stale = _row(news_id="stale", source="s2", title="Stale story", published_at=NOW - timedelta(hours=40))
    trusted = _row(news_id="trusted", title="Trusted story", source="s3", source_trust=0.95)
    weak = _row(news_id="weak", title="Weak story", source="s4", source_trust=0.2)
    ranked = rank_items([stale, weak, fresh, trusted], NOW)
    ids = [r["news_id"] for r in ranked]
    assert ids.index("fresh") < ids.index("stale")
    assert ids.index("trusted") < ids.index("weak")


def test_source_cap_prevents_single_outlet_dominance() -> None:
    rows = [
        _row(news_id=f"x{i}", title=f"Unique story {i}", source="Firehose", source_trust=0.99)
        for i in range(6)
    ]
    kept = rank_items(rows, NOW)
    assert len(kept) == 3
    assert rank_items(rows, NOW, max_per_source=0) == sorted(
        kept + [], key=lambda r: -r["score"]
    ) or len(rank_items(rows, NOW, max_per_source=0)) == 6


def test_payload_matches_response_model_contract() -> None:
    ranked = rank_items([_row()], NOW)
    payload = news_item_payload(ranked[0])
    assert payload["score"] == ranked[0]["score"]
    assert payload["score_components"]["source_trust"] == 0.9
    assert payload["also_covered_by"] == []
