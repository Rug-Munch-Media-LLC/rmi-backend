"""Unit tests for the multi-level news dedup module (``app.rag.dedup``).

Covers:
  1. Level 1 URL dedup — Redis ZSET membership with 30-day lazy TTL; fail-open
     when Redis is unavailable (auth failure / down).
  2. Level 2 title similarity — Qdrant vector search, cosine >= 0.95 → dup;
     fail-open when Qdrant / embedder is unreachable.
  3. Level 3 content fingerprint — boilerplate stripping + first-1000-chars +
     whitespace normalization; fail-open when Redis is down.
  4. Level 4 entity overlap — crypto entities (tokens, protocols, people,
     addresses), overlap coefficient > 0.8; fail-open when entities missing.
  5. Grouping (CryptoPanic-style): url → group_id mapping, member sets.
  6. ``check_duplicates`` orchestrator — order, stop-on-first-hit, fail-open.

Redis is faked with ``fakeredis``; Qdrant and the embedder are mocked so no
network or browser access happens in these tests.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fakeredis import FakeStrictRedis

from app.rag import dedup
from app.rag.dedup import (
    DedupResult,
    check_duplicates,
    content_fingerprint,
    extract_crypto_entities,
    is_duplicate_by_entities,
    is_duplicate_by_title,
    is_duplicate_content,
    is_duplicate_url,
    mark_content_ingested,
    mark_url_ingested,
    new_group_id,
    record_fp_group,
    record_group_membership,
    url_hash,
)

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fake_redis() -> FakeStrictRedis:
    """Point the dedup module at a fresh fakeredis instance per test."""
    r = FakeStrictRedis(decode_responses=True)
    dedup._redis_client = r
    yield r
    dedup._redis_client = None


@pytest.fixture
def qdrant_hit() -> SimpleNamespace:
    """A fake engine ``Hit`` with the fields dedup reads."""
    return SimpleNamespace(
        doc_id="crawl:orig123",
        score=0.97,
        text="Bitcoin exchange hacked, $BTC stolen. Uniswap liquidity drained.",
        metadata={
            "title": "Bitcoin exchange hacked, BTC stolen",
            "source_url": "https://cointelegraph.com/news/btc-hack",
            "published_at": "2026-08-03T09:00:00+00:00",
            "group_id": "grp:existing",
        },
    )


# ── Level 1: URL dedup ──────────────────────────────────────────────────


def test_url_dup_detected_after_mark(fake_redis) -> None:
    """Same URL → duplicate after first mark."""
    url = "https://cointelegraph.com/news/1"
    assert is_duplicate_url(url) is False
    mark_url_ingested(url)
    assert is_duplicate_url(url) is True


def test_url_dup_different_urls_not_duplicates(fake_redis) -> None:
    """Different URLs (even same path prefix) are not duplicates."""
    mark_url_ingested("https://cointelegraph.com/news/1")
    assert is_duplicate_url("https://decrypt.co/news/1") is False
    assert is_duplicate_url("https://cointelegraph.com/news/2") is False


def test_url_dup_uses_dedicated_key(fake_redis) -> None:
    """URL hashes live in the ``rag:dedup:urls`` ZSET."""
    mark_url_ingested("https://example.com/a")
    assert fake_redis.zscore("rag:dedup:urls", url_hash("https://example.com/a")) is not None


def test_url_dup_expired_member_is_not_duplicate(fake_redis, monkeypatch) -> None:
    """A member older than the 30-day TTL is lazily pruned → not a duplicate."""
    mark_url_ingested("https://example.com/old")
    expired_ts = time.time() - dedup.URL_DEDUP_TTL_DAYS * 86400 - 1
    fake_redis.zadd("rag:dedup:urls", {url_hash("https://example.com/old"): expired_ts})
    assert is_duplicate_url("https://example.com/old") is False
    assert fake_redis.zscore("rag:dedup:urls", url_hash("https://example.com/old")) is None


def test_url_dup_fail_open_when_redis_down(monkeypatch) -> None:
    """Redis auth failure / outage → allow through (not a duplicate)."""
    dedup._redis_client = None

    def _boom() -> None:
        raise RuntimeError("NOAUTH Authentication required")

    monkeypatch.setattr(dedup, "_get_redis", _boom)
    assert is_duplicate_url("https://example.com/x") is False
    mark_url_ingested("https://example.com/x")  # must not raise


# ── Level 3: content fingerprint ────────────────────────────────────────


def test_content_fingerprint_ignores_formatting() -> None:
    """Whitespace / case normalization → same fingerprint for same body."""
    a = "Bitcoin ETFs see $1B inflows.\n\nMore text here."
    b = "  bitcoin   etfs see $1b inflows. more text here.  "
    assert content_fingerprint(a) == content_fingerprint(b)


def test_content_fingerprint_strips_boilerplate() -> None:
    """Footer/nav/CTA lines are removed before hashing."""
    body = "Bitcoin exchange hacked. Funds drained from wallets."
    with_footer = (
        "Subscribe to our newsletter\n"
        "Follow us on X\n"
        "© 2026 Example Media. All rights reserved.\n" + body + "\nRelated articles: more news\n"
        "Privacy Policy"
    )
    # The two fingerprints match even though one carries boilerplate.
    assert content_fingerprint(with_footer) == content_fingerprint(body)


def test_content_fingerprint_uses_first_1000_chars() -> None:
    """Only the first 1000 chars of the body influence the fingerprint."""
    long_a = "word " * 600  # 3000 chars
    long_b = "word " * 600 + " DIFFERENT ENDING THAT IS IGNORED"
    assert content_fingerprint(long_a) == content_fingerprint(long_b)


def test_content_dup_detected_after_mark(fake_redis) -> None:
    """Same content fingerprint → duplicate after mark."""
    fp = content_fingerprint("Bitcoin exchange hacked. BTC stolen.")
    assert is_duplicate_content(fp, "news_v2") is False
    mark_content_ingested(fp, "news_v2")
    assert is_duplicate_content(fp, "news_v2") is True


def test_content_dup_scoped_to_collection(fake_redis) -> None:
    """Fingerprints are tracked per collection."""
    fp = content_fingerprint("Bitcoin hack")
    mark_content_ingested(fp, "news_v2")
    assert is_duplicate_content(fp, "news_v2") is True
    assert is_duplicate_content(fp, "scanner") is False


def test_content_dup_fail_open_when_redis_down(monkeypatch) -> None:
    """Redis unavailable → allow through."""
    dedup._redis_client = None

    def _boom() -> None:
        raise RuntimeError("NOAUTH Authentication required")

    monkeypatch.setattr(dedup, "_get_redis", _boom)
    assert is_duplicate_content(content_fingerprint("x"), "news_v2") is False


# ── Level 2: title similarity ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_title_dup_detected_above_threshold(qdrant_hit) -> None:
    """Qdrant hit with cosine >= 0.95 → duplicate."""
    with patch("app.rag.dedup._title_search", new=AsyncMock(return_value=[qdrant_hit])):
        assert await is_duplicate_by_title("Bitcoin exchange hacked, BTC stolen") is True


@pytest.mark.asyncio
async def test_title_below_threshold_not_duplicate() -> None:
    """Qdrant hit below 0.95 → not a duplicate."""
    hit = SimpleNamespace(doc_id="crawl:orig", score=0.80, text="x", metadata={"title": "x"})
    with patch("app.rag.dedup._title_search", new=AsyncMock(return_value=[hit])):
        assert await is_duplicate_by_title("Different title") is False


@pytest.mark.asyncio
async def test_title_dup_fail_open_on_qdrant_error() -> None:
    """Qdrant unreachable / embedder down → allow through."""

    async def _boom(_title: str, _collection: str, limit: int) -> None:
        raise RuntimeError("connection refused")

    with patch("app.rag.dedup._title_search", new=_boom):
        assert await is_duplicate_by_title("Bitcoin hack") is False


# ── Level 4: entity overlap ─────────────────────────────────────────────


def test_extract_crypto_entities_tokens_protocols_people() -> None:
    entities = extract_crypto_entities("$BTC surged as Uniswap liquidity drained. Vitalik commented on the hack.")
    assert "btc" in entities
    assert "uniswap" in entities
    assert "vitalik" in entities


def test_extract_crypto_entities_addresses() -> None:
    entities = extract_crypto_entities("Funds moved to 0x1234567890abcdef1234567890abcdef12345678 on Solana.")
    assert "0x1234567890abcdef1234567890abcdef12345678" in entities


def test_entity_overlap_duplicate() -> None:
    a = "Bitcoin exchange hacked. $BTC stolen. Uniswap LP drained."
    b = "BTC stolen in exchange hack. Uniswap pools drained."
    assert is_duplicate_by_entities(a, b) is True


def test_entity_overlap_below_threshold() -> None:
    a = "Bitcoin exchange hacked. $BTC stolen."
    b = "Ethereum NFT marketplace launched. $ETH fees low."
    assert is_duplicate_by_entities(a, b) is False


def test_entity_overlap_fail_open_on_empty() -> None:
    """No recognizable entities on either side → cannot decide → not dup."""
    assert is_duplicate_by_entities("a b c", "d e f") is False
    assert is_duplicate_by_entities("", "Bitcoin hack") is False


def test_entity_overlap_respects_threshold() -> None:
    a = "Bitcoin hack. Ethereum hack. Solana hack. Polygon hack."
    b = "Bitcoin hack. Ethereum hack. Solana hack. Arbitrum hack."
    # 3/4 shared → 0.75 overlap → below 0.8 → not a duplicate.
    assert is_duplicate_by_entities(a, b, threshold=0.8) is False


# ── Grouping ────────────────────────────────────────────────────────────


def test_new_group_id_is_stable() -> None:
    assert new_group_id("https://example.com/a") == new_group_id("https://example.com/a")
    assert new_group_id("https://example.com/a").startswith("grp:")


def test_record_group_membership_maps_url_to_group(fake_redis) -> None:
    record_group_membership("grp:g1", "https://example.com/a", "news_v2")
    assert fake_redis.hget("rag:dedup:url:group", url_hash("https://example.com/a")) == "grp:g1"
    assert fake_redis.smembers("rag:dedup:group:members:grp:g1") == {"https://example.com/a"}


def test_record_fp_group_maps_fingerprint(fake_redis) -> None:
    fp = content_fingerprint("Bitcoin hack body")
    record_fp_group(fp, "grp:g1", "news_v2")
    assert fake_redis.hget("rag:dedup:content:group:news_v2", fp) == "grp:g1"


# ── Orchestrator ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_duplicates_url_level(fake_redis) -> None:
    """Level 1 fires first and short-circuits the others."""
    mark_url_ingested("https://example.com/seen")
    with patch("app.rag.dedup._title_search", new=AsyncMock()) as search_mock:
        result = await check_duplicates(
            url="https://example.com/seen",
            title="Bitcoin hack",
            content="Bitcoin exchange hacked.",
            collection="news_v2",
        )
    search_mock.assert_not_awaited()
    assert result.is_duplicate is True
    assert result.level == "url"


@pytest.mark.asyncio
async def test_check_duplicates_content_level(fake_redis) -> None:
    """Level 3 fires when the fingerprint matches an existing article."""
    body = "Bitcoin exchange hacked. Funds drained."
    fp = content_fingerprint(body)
    mark_content_ingested(fp, "news_v2")
    record_fp_group(fp, "grp:old", "news_v2")
    with patch("app.rag.dedup._title_search", new=AsyncMock()) as search_mock:
        result = await check_duplicates(
            url="https://example.com/new",
            title="Bitcoin hack",
            content=body,
            collection="news_v2",
        )
    search_mock.assert_not_awaited()
    assert result.is_duplicate is True
    assert result.level == "content"
    assert result.group_id == "grp:old"


@pytest.mark.asyncio
async def test_check_duplicates_title_level(qdrant_hit) -> None:
    """Level 2 fires on a high-similarity Qdrant hit and carries the group."""
    with patch("app.rag.dedup._title_search", new=AsyncMock(return_value=[qdrant_hit])):
        result = await check_duplicates(
            url="https://example.com/variant",
            title="Bitcoin exchange hacked, BTC stolen",
            content="Bitcoin exchange hacked. BTC stolen.",
            collection="news_v2",
        )
    assert result.is_duplicate is True
    assert result.level == "title"
    assert result.group_id == "grp:existing"
    assert result.matched_doc_id == "crawl:orig123"


@pytest.mark.asyncio
async def test_check_duplicates_entity_level_within_window() -> None:
    """Level 4 fires on entity overlap + 6h window (CryptoPanic grouping)."""
    hit = SimpleNamespace(
        doc_id="crawl:orig",
        score=0.70,  # below title threshold — entity check applies
        text="Bitcoin exchange hacked. $BTC stolen. Uniswap liquidity drained.",
        metadata={
            "title": "BTC exchange breach",
            "source_url": "https://decrypt.co/news/btc-breach",
            "published_at": "2026-08-03T09:00:00+00:00",
            "group_id": "grp:story1",
        },
    )
    with patch("app.rag.dedup._title_search", new=AsyncMock(return_value=[hit])):
        result = await check_duplicates(
            url="https://theblock.co/news/btc-breach",
            title="Exchange hack drains Bitcoin",
            content="Bitcoin exchange hacked. BTC stolen. Uniswap pools drained. $BTC down.",
            collection="news_v2",
            published_at="2026-08-03T11:30:00+00:00",
        )
    assert result.is_duplicate is True
    assert result.level == "entity"
    assert result.group_id == "grp:story1"


@pytest.mark.asyncio
async def test_check_duplicates_entity_skipped_outside_window() -> None:
    """Entity overlap is ignored when articles are >6h apart (fail-open)."""
    hit = SimpleNamespace(
        doc_id="crawl:old",
        score=0.70,
        text="Bitcoin exchange hacked. $BTC stolen. Uniswap liquidity drained.",
        metadata={
            "title": "BTC exchange breach",
            "source_url": "https://decrypt.co/news/btc-breach",
            "published_at": "2026-08-01T09:00:00+00:00",  # 2 days ago
            "group_id": "grp:story1",
        },
    )
    with patch("app.rag.dedup._title_search", new=AsyncMock(return_value=[hit])):
        result = await check_duplicates(
            url="https://theblock.co/news/btc-breach",
            title="Exchange hack drains Bitcoin",
            content="Bitcoin exchange hacked. BTC stolen. Uniswap pools drained. $BTC down.",
            collection="news_v2",
            published_at="2026-08-03T11:30:00+00:00",
        )
    assert result.is_duplicate is False


@pytest.mark.asyncio
async def test_check_duplicates_no_dup_generates_group() -> None:
    """Fresh story → not a duplicate, gets a fresh group_id."""
    with patch("app.rag.dedup._title_search", new=AsyncMock(return_value=[])):
        result = await check_duplicates(
            url="https://example.com/fresh",
            title="Brand new story",
            content="A completely unique article body with no overlap anywhere.",
            collection="news_v2",
        )
    assert result.is_duplicate is False
    assert result.group_id == new_group_id("https://example.com/fresh")


@pytest.mark.asyncio
async def test_check_duplicates_all_backends_down_fail_open() -> None:
    """Redis + Qdrant + embedder all down → article still allowed through."""
    dedup._redis_client = None

    def _boom() -> None:
        raise RuntimeError("NOAUTH Authentication required")

    async def _search_boom(_title: str, _collection: str, limit: int) -> None:
        raise RuntimeError("qdrant unreachable")

    with (
        patch.object(dedup, "_get_redis", side_effect=_boom),
        patch("app.rag.dedup._title_search", new=_search_boom),
    ):
        result = await check_duplicates(
            url="https://example.com/down",
            title="Bitcoin hack",
            content="Bitcoin exchange hacked.",
            collection="news_v2",
            published_at="2026-08-03T11:30:00+00:00",
        )
    assert result.is_duplicate is False
    assert result.group_id == new_group_id("https://example.com/down")


@pytest.mark.asyncio
async def test_check_duplicates_returns_dedup_result_type() -> None:
    """check_duplicates returns a typed DedupResult."""
    with patch("app.rag.dedup._title_search", new=AsyncMock(return_value=[])):
        result = await check_duplicates("https://example.com/a", "T", "Body", "news_v2")
    assert isinstance(result, DedupResult)
