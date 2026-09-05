"""Story-cluster → RAG wiring tests.

Verifies the dedup contract: the same article across many feeds collapses
to ONE RAG doc (``news:{cluster_id}``) carrying the rating card +
``source_count`` + per-story source distribution.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domains.news.clusterer import NewsItem as ClusterNewsItem, build_story_rag_docs, story_doc_id

_T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

_TITLE = "Bitcoin ETF sees record inflows as institutions pile in"


def _item(idx: str, source: str, title: str = _TITLE, minutes: int = 0) -> ClusterNewsItem:
    return ClusterNewsItem(
        id=idx,
        title=title,
        body=f"Bitcoin ETF inflows hit a new record today. {idx}",
        source=source,
        url=f"https://example.com/{idx}",
        published_at=_T0 + timedelta(minutes=minutes),
        sentiment=0.5,
    )


def test_dedup_collapses_duplicate_headlines_to_one_doc() -> None:
    """20 sources covering the same story → exactly 1 RAG doc, source_count=20."""
    items = [_item(f"i{n}", f"Source {n}", minutes=n * 5) for n in range(20)]
    docs = build_story_rag_docs(items)
    assert len(docs) == 1

    doc = docs[0]
    meta = doc["metadata"]
    assert meta["cluster_id"]
    assert meta["source_count"] == 20
    assert len(meta["sources"]) == 20
    assert len(meta["source_urls"]) == 20
    assert len(meta["item_ids"]) == 20
    assert doc["doc_id"] == story_doc_id(meta["cluster_id"])
    assert doc["doc_id"].startswith("news:")
    assert meta["news_id"] == doc["doc_id"]


def test_story_doc_id_stable_across_batches() -> None:
    """Re-clustering the same story with different members keeps the doc_id."""
    batch1 = [_item("a1", "CoinDesk"), _item("a2", "The Block", minutes=5)]
    batch2 = [
        _item("x1", "CoinDesk", minutes=10),
        _item("x2", "The Block", minutes=15),
        _item("x3", "Decrypt", minutes=20),
    ]

    docs1 = build_story_rag_docs(batch1)
    docs2 = build_story_rag_docs(batch2)
    assert len(docs1) == 1
    assert len(docs2) == 1
    assert docs1[0]["doc_id"] == docs2[0]["doc_id"]
    # Merge semantics: later batch carries MORE sources on the same doc.
    assert docs2[0]["metadata"]["source_count"] == 3


def test_story_docs_carry_rating_card() -> None:
    """Every RAG doc carries sentiment_label / bias / factual_rating / source_tier."""
    docs = build_story_rag_docs([_item("r1", "CoinDesk"), _item("r2", "Reddit", minutes=5)])
    assert len(docs) == 1
    meta = docs[0]["metadata"]
    for key in ("sentiment_label", "bias", "factual_rating", "source_tier", "cluster_id", "source_count"):
        assert key in meta, f"missing {key}"
    # CoinDesk is a known tier-1 source (representative = longest title).
    assert meta["source_tier"] == 1
    assert meta["bias"] == "center"
    assert meta["factual_rating"] == "high"
    assert meta["sentiment_label"] in {"bullish", "slightly_bullish", "neutral", "slightly_bearish", "bearish"}
    # Both sources are center-bias → uniform distribution.
    assert meta["bias_distribution"] == {"center": 2}


def test_unrelated_headlines_stay_separate() -> None:
    """Different stories must NOT collapse into one doc."""
    items = [
        _item("u1", "CoinDesk", title="Bitcoin price drops after Fed minutes"),
        _item("u2", "The Block", title="Ethereum gas fees hit yearly low", minutes=5),
    ]
    docs = build_story_rag_docs(items)
    assert len(docs) == 2
    assert docs[0]["doc_id"] != docs[1]["doc_id"]
    assert all(d["metadata"]["source_count"] == 1 for d in docs)


# ── Databus end-to-end: index_databus_news dedups 20 sources → 1 ingest ──


def _article(i: int) -> dict[str, object]:
    return {
        "title": _TITLE,
        "url": f"https://example.com/{i}",
        "summary": f"Bitcoin ETF inflows hit a new record today. {i}",
        "published": "Wed, 30 Jul 2026 10:00:00 GMT",
        "source": f"Source {i}",
        "source_tier": 2,
        "source_quality": 0.7,
        "quality_score": 0.8,
        "sentiment": {"sentiment": "bullish", "score": 0.5, "confidence": 0.7},
        "categories": ["bitcoin", "etf"],
        "content_hash": f"hash{i}",
        "indexed_at": "2026-08-01T12:00:00+00:00",
    }


def test_index_databus_news_dedups_to_one_story_doc(monkeypatch: object) -> None:
    """20 duplicate articles from 20 sources → one ingest call, source_count=20."""
    import asyncio

    articles = [_article(i) for i in range(20)]
    fake_feed = {
        "articles": articles,
        "total_fetched": 20,
        "after_dedup": 20,
        "returned": 20,
        "stats": {},
        "sources_used": [],
        "generated_at": "2026-08-01T12:00:00+00:00",
        "source": "news_intelligence_engine",
    }
    fake_calls: list[dict[str, object]] = []

    async def _fake_ingest(**kw: object) -> bool:
        fake_calls.append(kw)
        return True

    monkeypatch.setattr("app.databus.news_intel.aggregate_all_news", _async_ok(fake_feed))
    monkeypatch.setattr("app.integrations.rugbrain_client.rugbrain_ingest", _fake_ingest)

    from app.databus.rag_ingestion import index_databus_news

    result = asyncio.run(index_databus_news(limit=50))

    assert result["status"] == "ok"
    assert len(fake_calls) == 1, f"expected 1 story ingest, got {len(fake_calls)}"
    call = fake_calls[0]
    meta = call["metadata"]
    assert isinstance(meta, dict)
    assert meta["source_count"] == 20
    assert meta["cluster_id"]
    assert call["doc_id"] == story_doc_id(meta["cluster_id"])
    assert call["collection"] == "news_v2"
    # Rating card flows into the story doc.
    for key in ("sentiment_label", "bias", "factual_rating", "source_tier"):
        assert key in meta, f"missing {key}"
    assert meta["categories"] == ["bitcoin", "etf"]
    assert result["stories"] == 1
    assert result["ingested"] == 1


def _async_ok(value: object):
    async def _inner(*args: object, **kwargs: object) -> object:
        return value

    return _inner
