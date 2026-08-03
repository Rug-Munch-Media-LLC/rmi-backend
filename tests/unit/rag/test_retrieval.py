"""Unit tests for news retrieval ranking boosts (app.rag.retrieval).

Covers:
  1. ``extract_crypto_entities`` — ticker/name canonicalization.
  2. ``apply_news_boosts`` — freshness multipliers (breaking/recent/stale).
  3. ``apply_news_boosts`` — entity-overlap multiplier.
  4. ``apply_news_boosts`` — no-op for non-news collections.
  5. ``apply_news_boosts`` — re-sorts by boosted final_score.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.rag.retrieval import (
    ScoredHit,
    apply_news_boosts,
    extract_crypto_entities,
)

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


def _news_hit(
    *,
    content: str = "Bitcoin ETF inflows surge",
    score: float = 0.8,
    published: str = "2026-08-03T11:30:00+00:00",
    title: str = "Bitcoin ETF inflows surge",
    collection: str = "news_v2",
) -> ScoredHit:
    return ScoredHit(
        content=content,
        score=score,
        final_score=score,
        collection=collection,
        doc_id=f"news:{content[:12]}",
        metadata={"title": title, "published_at": published},
    )


# ── extract_crypto_entities ────────────────────────────────────────────


def test_extract_crypto_entities_tickers_and_names() -> None:
    assert extract_crypto_entities("BTC price movement today") == ["bitcoin"]
    assert extract_crypto_entities("Ethereum and Uniswap UNI news") == ["ethereum", "uniswap"]
    assert extract_crypto_entities("SOL liquidations on Jupiter") == ["jupiter", "solana"]


def test_extract_crypto_entities_empty_and_unknown() -> None:
    assert extract_crypto_entities("") == []
    assert extract_crypto_entities("the weather is nice") == []
    # Ambiguous short words are not treated as tickers.
    assert extract_crypto_entities("I need to go to the store ASAP") == []


def test_extract_crypto_entities_canonicalizes_variants() -> None:
    # "BTC" and "Bitcoin" both canonicalize to the same label.
    assert set(extract_crypto_entities("BTC is up")) == set(extract_crypto_entities("Bitcoin is up"))
    assert set(extract_crypto_entities("UNI")) | set(extract_crypto_entities("Uniswap")) == {"uniswap"}


# ── freshness boost ────────────────────────────────────────────────────


def test_news_boost_breaking_news_under_one_hour() -> None:
    hit = _news_hit(published="2026-08-03T11:30:00+00:00")  # 30 min old
    apply_news_boosts([hit], "weather forecast", collection="news_v2", now=NOW)
    assert hit.metadata["news_boost"] == 1.5
    assert hit.final_score == 0.8 * 1.5


def test_news_boost_recent_under_six_hours() -> None:
    hit = _news_hit(published="2026-08-03T08:00:00+00:00")  # 4h old
    apply_news_boosts([hit], "weather forecast", collection="news_v2", now=NOW)
    assert hit.metadata["news_boost"] == 1.2


def test_news_boost_stale_over_72_hours_penalized() -> None:
    hit = _news_hit(published="2026-07-25T00:00:00+00:00")  # >9 days old
    apply_news_boosts([hit], "weather forecast", collection="news_v2", now=NOW)
    assert hit.metadata["news_boost"] == 0.5
    assert hit.final_score == 0.8 * 0.5


def test_news_boost_ignores_missing_published_at() -> None:
    hit = ScoredHit(
        content="general market wrap",
        score=0.8,
        final_score=0.8,
        collection="news_v2",
        doc_id="news:no-date",
        metadata={},
    )
    apply_news_boosts([hit], "weather forecast", collection="news_v2", now=NOW)
    assert "news_boost" not in hit.metadata
    assert hit.final_score == 0.8


# ── entity overlap boost ───────────────────────────────────────────────


def test_news_boost_entity_overlap_multiplier() -> None:
    hit = _news_hit(content="Bitcoin miners and BTC ETF flows", published="2026-07-20T00:00:00+00:00")
    # Freshness applies (stale x0.5) then entity overlap (bitcoin x1.1).
    apply_news_boosts([hit], "Bitcoin price movement", collection="news_v2", now=NOW)
    assert hit.final_score == 0.8 * 0.5 * 1.1


def test_news_boost_entity_overlap_uses_metadata_entities() -> None:
    hit = ScoredHit(
        content="A neutral summary of market moves",
        score=0.7,
        final_score=0.7,
        collection="news_v2",
        doc_id="news:meta-entities",
        metadata={"published_at": "2026-07-20T00:00:00+00:00", "tokens_mentioned": ["bitcoin"], "chains_mentioned": []},
    )
    apply_news_boosts([hit], "Bitcoin price", collection="news_v2", now=NOW)
    assert hit.final_score == 0.7 * 0.5 * 1.1


def test_news_boost_no_entity_no_freshness_is_noop() -> None:
    hit = _news_hit(published="2026-08-02T12:00:00+00:00")  # 24h old: no bucket
    apply_news_boosts([hit], "weather forecast", collection="news_v2", now=NOW)
    assert "news_boost" not in hit.metadata
    assert hit.final_score == 0.8


# ── collection gating ──────────────────────────────────────────────────


def test_news_boost_noop_for_non_news_collection() -> None:
    hit = _news_hit(published="2026-08-03T11:30:00+00:00", collection="scams")
    apply_news_boosts([hit], "Bitcoin price", collection="scams", now=NOW)
    assert "news_boost" not in hit.metadata
    assert hit.final_score == 0.8


def test_news_boost_skips_non_news_hits_in_mixed_list() -> None:
    news = _news_hit(published="2026-08-03T11:30:00+00:00", collection="news_v2")
    scam = _news_hit(content="honeypot pattern", published="2026-08-03T11:30:00+00:00", collection="scams")
    apply_news_boosts([scam, news], "weather forecast", collection="news_v2", now=NOW)
    assert news.metadata["news_boost"] == 1.5
    assert "news_boost" not in scam.metadata


# ── ordering ───────────────────────────────────────────────────────────


def test_news_boost_resorts_by_boosted_score() -> None:
    breaking = _news_hit(content="breaking news", score=0.6, published="2026-08-03T11:45:00+00:00")
    older_higher = _news_hit(content="older article", score=0.9, published="2026-07-20T00:00:00+00:00")
    out = apply_news_boosts([older_higher, breaking], "Bitcoin price", collection="news_v2", now=NOW)
    # breaking: 0.6 * 1.5 * 1.1 = 0.99 ; older: 0.9 * 0.5 * 1.1 = 0.495
    assert out[0].content == "breaking news"
