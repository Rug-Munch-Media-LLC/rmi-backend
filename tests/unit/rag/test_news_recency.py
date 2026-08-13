"""Recency wiring tests — DataBus news → RAG temporal decay.

Covers:
  1. ``_parse_published_at`` — accepts the databus ``published`` key so
     news_intel-fetched articles get a freshness signal.
  2. ``apply_news_boosts`` — recent articles surface first for
     time-sensitive queries (dry search ordering).
  3. ``_normalize_published`` — databus timestamp normalization.
  4. ``check_news_staleness`` — flags multi-day lag and reports the
     freshest live source.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.databus.rag_ingestion import _normalize_published, check_news_staleness
from app.rag.retrieval import ScoredHit, _parse_published_at, apply_news_boosts

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


def _news_hit(
    *, published: str | int = "2026-08-03T11:30:00+00:00", score: float = 0.8, meta_key: str = "published_at"
) -> ScoredHit:
    return ScoredHit(
        content="Bitcoin ETF inflows surge",
        score=score,
        final_score=score,
        collection="news_v2",
        doc_id=f"news:{meta_key}:{published}",
        metadata={"title": "Bitcoin ETF inflows surge", meta_key: published},
    )


# ── _parse_published_at aliases ───────────────────────────────────────


def test_parse_published_at_accepts_databus_published_key() -> None:
    hit = _news_hit(published="2026-08-03T11:30:00+00:00", meta_key="published")
    dt = _parse_published_at(hit)
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt == datetime(2026, 8, 3, 11, 30, tzinfo=UTC)


def test_parse_published_at_accepts_epoch() -> None:
    hit = _news_hit(published=1785763800, meta_key="published_at")
    dt = _parse_published_at(hit)
    assert dt is not None


def test_parse_published_at_none_for_garbage() -> None:
    hit = _news_hit(published="not-a-date", meta_key="published")
    assert _parse_published_at(hit) is None


# ── recency boost ordering (dry search) ───────────────────────────────


def test_news_boost_surfaces_recent_first() -> None:
    """Same relevance, but the fresh article outranks the stale one."""
    recent = _news_hit(published="2026-08-03T11:30:00+00:00")  # 30 min old
    stale = _news_hit(published="2026-07-25T00:00:00+00:00")  # >9 days old
    hits = apply_news_boosts([stale, recent], "weather forecast", collection="news_v2", now=NOW)
    assert hits[0].doc_id == recent.doc_id
    assert recent.metadata["news_boost"] == 1.5
    assert stale.metadata["news_boost"] == 0.5


def test_news_boost_recent_surfaces_even_when_base_lower() -> None:
    """A breaking (<1h) article beats an older, higher-similarity one."""
    recent = _news_hit(published="2026-08-03T11:45:00+00:00", score=0.8)  # 15 min old
    older = _news_hit(published="2026-08-02T12:00:00+00:00", score=0.9)  # 24h old: no bucket
    hits = apply_news_boosts([older, recent], "weather forecast", collection="news_v2", now=NOW)
    assert hits[0].doc_id == recent.doc_id
    assert recent.final_score == 0.8 * 1.5  # 1.2 > 0.9


# ── databus timestamp normalization ───────────────────────────────────


def test_normalize_published_iso_z_suffix() -> None:
    assert _normalize_published("2026-08-03T11:30:00Z").endswith("+00:00")


def test_normalize_published_rss_rfc2822() -> None:
    assert _normalize_published("Wed, 30 Jul 2026 10:00:00 GMT") != ""


def test_normalize_published_epoch_and_garbage() -> None:
    assert _normalize_published(1785763800) != ""
    assert _normalize_published("1785763800") != ""
    assert _normalize_published(None) == ""
    assert _normalize_published("garbage") == ""


# ── staleness check ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_staleness_flags_multi_day_lag(monkeypatch: pytest.MonkeyPatch) -> None:
    ten_days_ago = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)

    async def fake_latest() -> tuple[datetime | None, dict[str, object] | None]:
        return ten_days_ago, {"source_name": "Cointelegraph", "published_at": ten_days_ago.isoformat()}

    async def fake_aggregate(limit: int = 5, **kw) -> dict[str, object]:
        return {
            "articles": [
                {"title": "Fresh story", "source": "CryptoPanic", "published_at": "2026-08-03T10:00:00+00:00"}
            ],
            "generated_at": "2026-08-03T12:00:00+00:00",
        }

    monkeypatch.setattr("app.databus.rag_ingestion._latest_news_v2_timestamp", fake_latest)
    monkeypatch.setattr("app.databus.news_intel.aggregate_all_news", fake_aggregate)

    result = await check_news_staleness()
    assert result["stale"] is True
    assert result["status"] == "stale"
    assert result["lag_hours"] > 24 * 9
    assert result["remediation"] is not None
    assert result["live"]["freshest_article"]["source"] == "CryptoPanic"


@pytest.mark.asyncio
async def test_staleness_fresh_when_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)

    async def fake_latest() -> tuple[datetime | None, dict[str, object] | None]:
        return two_hours_ago, {"source_name": "Cointelegraph"}

    async def fake_aggregate(limit: int = 5, **kw) -> dict[str, object]:
        return {"articles": [], "generated_at": "2026-08-03T12:00:00+00:00"}

    monkeypatch.setattr("app.databus.rag_ingestion._latest_news_v2_timestamp", fake_latest)
    monkeypatch.setattr("app.databus.news_intel.aggregate_all_news", fake_aggregate)

    result = await check_news_staleness()
    assert result["stale"] is False
    assert result["status"] == "fresh"
    assert result["lag_hours"] <= 48
