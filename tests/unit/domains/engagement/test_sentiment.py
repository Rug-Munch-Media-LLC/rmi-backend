"""Sentiment contract: buckets are derived, and a score is never invented."""

from __future__ import annotations

from fastapi.testclient import TestClient  # noqa: TC002 — pytest keeps it runtime

from app.domains.engagement.kinds import bucket_for
from app.domains.engagement.sentiment import bucket_counts, derive
from tests.unit.domains.engagement.conftest import as_user, post

REACTIONS = "/api/v1/articles/art-1/reactions"
SENTIMENT = "/api/v1/articles/art-1/sentiment"


def test_kind_to_bucket_mapping() -> None:
    assert bucket_for("bullish") == "positive"
    assert bucket_for("like") == "positive"
    assert bucket_for("important") == "neutral"
    assert bucket_for("scam") == "negative"
    assert bucket_for("bearish") == "negative"
    assert bucket_for("unknown") == "neutral"


def test_bucket_counts_ignore_unknown_kinds() -> None:
    assert bucket_counts({"bullish": 2, "dislike": 1, "important": 3, "clap": 9}) == (2, 3, 1)


def test_no_signal_returns_null_score_with_reason() -> None:
    result = derive({})

    assert result.score is None
    assert result.reason == "no_reactions"
    assert result.sample_size == 0
    assert result.has_score is False


def test_thin_signal_keeps_counts_but_withholds_score() -> None:
    result = derive({"bullish": 1, "bearish": 1})

    assert (result.positive, result.negative) == (1, 1)
    assert result.score is None
    assert result.reason == "insufficient_reactions"


def test_score_is_polarity_weighted() -> None:
    result = derive({"bullish": 2, "bearish": 1})

    assert result.sample_size == 3
    assert result.score == 33.33
    assert result.reason == "reactions"


def test_classifier_only_is_labelled_and_clamped() -> None:
    result = derive({}, classifier_score=250.0)

    assert result.score == 100.0
    assert result.reason == "classifier_only"


def test_classifier_blends_with_reactions() -> None:
    result = derive({"like": 3}, classifier_score=-100.0)

    assert result.reason == "reactions_plus_classifier"
    assert result.score == 0.0


def test_endpoint_reports_no_signal(client: TestClient) -> None:
    body = client.get(SENTIMENT).json()

    assert body["score"] is None
    assert body["reason"] == "no_reactions"
    assert body["sample_size"] == 0
    assert body["updated_at"] is None


def test_endpoint_withholds_score_below_threshold(client: TestClient) -> None:
    as_user(client, "u1")
    post(client, REACTIONS, {"kind": "bullish"})
    as_user(client, "u2")
    post(client, REACTIONS, {"kind": "bearish"})

    body = client.get(SENTIMENT).json()
    assert body["positive"] == 1
    assert body["negative"] == 1
    assert body["score"] is None
    assert body["reason"] == "insufficient_reactions"


def test_endpoint_publishes_score_once_there_is_signal(client: TestClient) -> None:
    for user in ("u1", "u2"):
        as_user(client, user)
        post(client, REACTIONS, {"kind": "bullish"})
    as_user(client, "u3")
    post(client, REACTIONS, {"kind": "bearish"})

    body = client.get(SENTIMENT).json()
    assert body["sample_size"] == 3
    assert body["score"] == 33.33
    assert body["reason"] == "reactions"
    assert body["updated_at"] is not None


def test_unreact_updates_the_rollup(client: TestClient) -> None:
    for user in ("u1", "u2"):
        as_user(client, user)
        post(client, REACTIONS, {"kind": "bullish"})
    as_user(client, "u3")
    post(client, REACTIONS, {"kind": "bearish"})
    client.delete(f"{REACTIONS}/bearish")

    body = client.get(SENTIMENT).json()
    assert body["negative"] == 0
    assert body["sample_size"] == 2
    # 2 reactions is below MIN_SIGNAL=3: no score is published, by design.
    assert body["score"] is None
    assert body["reason"] == "insufficient_reactions"
