"""End-to-end content pipeline integration tests.

Covers the full Telegram → normalize → quality-score → dedup → embed
(Qdrant via rugbrain) → rerank → feed-retrieval path with every external
dependency (telethon, httpx, Redis, Qdrant) mocked.

Layers under test:
  * ``app.domains.news_ingest.ingest`` — read_channel_messages /
    sweep_channels / dedup
  * ``app.domains.news_ingest.normalize`` — channel_message_to_article
  * ``app.domains.news_ingest.scheduler`` — run_ingest_cycle (quality gate)
  * ``app.feed_quality_pipeline.pipeline`` — run_quality_pipeline
  * ``app.integrations.rugbrain_client`` — rugbrain_ingest /
    rugbrain_news_rerank (fail-open contract)
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.news_ingest.ingest import (
    dedupe_articles,
    read_channel_messages,
    sweep_channels,
)
from app.domains.news_ingest.normalize import channel_message_to_article
from app.domains.news_ingest.scheduler import (
    NEWS_COLLECTION,
    QUALITY_GATE_THRESHOLD,
    run_ingest_cycle,
)
from app.feed_quality_pipeline.pipeline import run_quality_pipeline
from app.integrations.rugbrain_client import rugbrain_news_rerank

# ── Fixtures / helpers ──────────────────────────────────────────────────────

SAMPLE_NEWS_TEXT = (
    "Bitcoin ETF inflows hit record high as BlackRock adds $500M in a single day\n"
    "Institutional demand keeps accelerating.\n"
    "Full story: https://www.coindesk.com/markets/bitcoin-etf-record-inflows"
)

CHANNELS = [{"username": "cointelegraph", "category": "crypto_news"}]


def _tg_message(message_id: int = 101, text: str = SAMPLE_NEWS_TEXT, channel: str = "Cointelegraph"):
    """Duck-typed Telethon message (attributes read via getattr)."""
    return SimpleNamespace(
        id=message_id,
        message=text,
        chat=SimpleNamespace(title=channel, username=channel.lower()),
        date=datetime.now(UTC),
        media=None,
        views=1200,
        reactions=None,
        fwd_from=None,
    )


def _fake_telethon_client(messages: list) -> SimpleNamespace:
    """Client exposing ``_client.iter_messages`` like the headless wrapper."""

    def _iter(entity, limit=50):
        async def _gen():
            for msg in messages:
                yield msg

        return _gen()

    return SimpleNamespace(_client=SimpleNamespace(iter_messages=_iter))


@pytest.fixture
def no_redis_dedup():
    """Neutralize the shared Redis dedup sets (fail-open contract)."""
    with (
        patch("app.utils.dedup.is_duplicate_content", return_value=False) as seen,
        patch("app.utils.dedup.mark_content_ingested") as mark,
    ):
        yield {"seen": seen, "mark": mark}


@pytest.fixture
def mock_rugbrain():
    """Mock rugbrain ingest + content-stats writers (no network, no DB)."""
    with (
        patch(
            "app.integrations.rugbrain_client.rugbrain_ingest",
            new_callable=AsyncMock,
            return_value=True,
        ) as ingest,
        patch(
            "app.domains.content.content_stats.record_fetch",
            new_callable=AsyncMock,
        ),
        patch(
            "app.domains.content.content_stats.record_channel_errors",
            new_callable=AsyncMock,
        ),
    ):
        yield ingest


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_redis_dedup")
async def test_full_telegram_pipeline(mock_rugbrain):
    """Telegram message → normalize → quality pipeline → rugbrain ingest."""
    client = _fake_telethon_client([_tg_message()])

    stats = await run_ingest_cycle(client, channels=CHANNELS, limit=10)

    # Normalize + sweep produced exactly one fresh article.
    assert stats["status"] == "ok"
    assert stats["swept"] == 1
    assert stats["fresh"] == 1
    assert stats["ingested"] == 1
    assert stats["errors"] == []

    # rugbrain_ingest called with the news collection + telegram source and
    # a metadata payload carrying the quality score.
    mock_rugbrain.assert_awaited_once()
    kwargs = mock_rugbrain.await_args.kwargs
    assert kwargs["collection"] == NEWS_COLLECTION == "news_v2"
    assert kwargs["source"] == "telegram"
    assert kwargs["doc_id"].startswith("tg_news:Cointelegraph:")
    metadata = kwargs["metadata"]
    assert metadata["kind"] == "news_article"
    assert metadata["source_channel"] == "Cointelegraph"
    assert 0.0 <= metadata["quality_score"] <= 1.0
    assert metadata["quality_score"] > 0.0


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_redis_dedup")
async def test_quality_gate_filters_low_score(mock_rugbrain):
    """An article scoring below the 0.3 gate is dropped before ingest."""
    client = _fake_telethon_client([_tg_message()])

    # Force the quality pipeline to return a sub-threshold score.
    low_score_result = [{"_idx": 0, "quality_score": 0.1}]
    with patch(
        "app.domains.news_ingest.scheduler.run_quality_pipeline",
        new_callable=AsyncMock,
        return_value=low_score_result,
    ):
        stats = await run_ingest_cycle(client, channels=CHANNELS, limit=10)

    assert stats["fresh"] == 0
    assert stats["ingested"] == 0
    mock_rugbrain.assert_not_awaited()
    assert QUALITY_GATE_THRESHOLD == 0.3


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_redis_dedup")
async def test_dedup_prevents_duplicate_ingest(mock_rugbrain):
    """The same story from two channels collapses into a single ingest."""
    same_story = (
        "Bitcoin ETF inflows hit record high as BlackRock adds $500M in a single day\n"
        "Source: https://www.coindesk.com/markets/bitcoin-etf-record-inflows"
    )
    client = _fake_telethon_client(
        [
            _tg_message(message_id=1, text=same_story, channel="Cointelegraph"),
            _tg_message(message_id=2, text=same_story, channel="WatcherGuru"),
        ]
    )
    two_channels = [
        {"username": "cointelegraph", "category": "crypto_news"},
        {"username": "watcherguru", "category": "crypto_news"},
    ]

    stats = await run_ingest_cycle(client, channels=two_channels, limit=10)

    # Both messages normalize, but dedup keeps only the first.
    assert stats["swept"] == 1
    assert stats["fresh"] == 1
    assert stats["ingested"] == 1
    mock_rugbrain.assert_awaited_once()

    # Pure-layer sanity: dedupe_articles alone also collapses the pair.
    articles = []
    for msg in (_tg_message(1, same_story), _tg_message(2, same_story, "WatcherGuru")):
        article = channel_message_to_article(
            SimpleNamespace(
                channel="WatcherGuru",
                channel_username="watcherguru",
                message_id=msg.id,
                text=msg.message,
                media_type="text",
                posted_at=msg.date,
                forwarded_from=None,
                views=None,
                reactions=None,
            ),
            "crypto_news",
        )
        assert article is not None
        articles.append(article)
    assert len(dedupe_articles(articles)) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_redis_dedup")
async def test_read_channel_messages_and_normalize():
    """read_channel_messages converts duck-typed Telethon messages."""
    client = _fake_telethon_client([_tg_message(message_id=7)])

    messages = await read_channel_messages(client, "cointelegraph", limit=5)

    assert len(messages) == 1
    assert messages[0].message_id == 7
    assert messages[0].channel_username == "cointelegraph"

    article = channel_message_to_article(messages[0], "crypto_news")
    assert article is not None
    assert "Bitcoin ETF" in article.title
    assert article.source_url == "https://www.coindesk.com/markets/bitcoin-etf-record-inflows"
    assert article.content_hash


@pytest.mark.asyncio
async def test_quality_pipeline_scores_and_sorts():
    """run_quality_pipeline annotates quality_score and sorts descending."""
    good = {
        "_idx": 0,
        "id": "a",
        "title": "Ethereum staking yields climb as institutional demand surges",
        "content": "Ethereum staking yields climbed again this week as institutions "
        "added ETH to their treasuries, pushing network participation to a new high.",
        "url": "https://example.com/eth",
        "author": "cointelegraph",
        "published": datetime.now(UTC).isoformat(),
        "categories": ["defi"],
        "source": "telegram",
    }
    mediocre = dict(good, _idx=1, id="b", title="Weekly market update", url="")

    scored = await run_quality_pipeline([mediocre, good], provider_group="news", source="telegram")

    assert len(scored) == 2
    scores = [item["quality_score"] for item in scored]
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores == sorted(scores, reverse=True)
    by_id = {item["id"]: item for item in scored}
    assert by_id["a"]["quality_score"] >= by_id["b"]["quality_score"]


@pytest.mark.asyncio
async def test_rerank_returns_relevant_results():
    """rugbrain_news_rerank orders results by rerank relevance score."""
    # The rugbrain endpoint returns items already ranked by rerank score.
    items = [
        {"id": "relevant", "title": "Bitcoin ETF record inflows", "score": 0.94},
        {"id": "mid", "title": "Bitcoin mining update", "score": 0.55},
        {"id": "irrelevant", "title": "Unrelated story", "score": 0.12},
    ]
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"items": items}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.integrations.rugbrain_client.httpx.AsyncClient", return_value=mock_client):
        result = await rugbrain_news_rerank("bitcoin etf inflows", limit=3)

    assert result is not None
    assert [item["id"] for item in result] == ["relevant", "mid", "irrelevant"]
    scores = [item["score"] for item in result]
    assert scores == sorted(scores, reverse=True)
    # Request carried the query + limit.
    body = mock_client.post.await_args.kwargs["json"]
    assert body["query"] == "bitcoin etf inflows"
    assert body["limit"] == 3


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_rugbrain")
async def test_failopen_on_qdrant_down(no_redis_dedup):
    """Qdrant/rugbrain outage never crashes the pipeline — fail-open."""
    # 1. Rerank lane: Qdrant direct search raises → rerank returns None.
    with (
        patch(
            "app.integrations.rugbrain_client._qdrant_direct_search",
            new_callable=AsyncMock,
            side_effect=RuntimeError("qdrant down"),
        ),
        patch(
            "app.integrations.rugbrain_client.httpx.AsyncClient",
            side_effect=RuntimeError("network down"),
        ),
    ):
        reranked = await rugbrain_news_rerank("bitcoin", limit=5)
    assert reranked is None  # caller falls back to recency ranking

    # 2. Ingest cycle: rugbrain ingest raises → cycle still completes.
    client = _fake_telethon_client([_tg_message()])
    with patch(
        "app.integrations.rugbrain_client.rugbrain_ingest",
        new_callable=AsyncMock,
        side_effect=RuntimeError("qdrant down"),
    ):
        stats = await run_ingest_cycle(client, channels=CHANNELS, limit=10)

    assert stats["status"] == "ok"
    assert stats["fresh"] == 1
    assert stats["ingested"] == 0  # nothing persisted, nothing marked seen
    assert stats["errors"]  # error recorded for monitoring
    # Dedup keys were NOT marked — a transient outage must not hide the story.
    no_redis_dedup["mark"].assert_not_called()


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_redis_dedup")
async def test_sweep_channels_failopen_per_channel():
    """A failing channel degrades to empty without aborting the sweep."""

    class _BrokenClient:
        _client = None  # falls through to read_messages path → no method → []

    articles = await sweep_channels(_BrokenClient(), CHANNELS, limit=10)
    assert articles == []
