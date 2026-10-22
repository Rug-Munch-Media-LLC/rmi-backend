"""Tests for the single news body-quality scorer."""

from __future__ import annotations

import pytest

from app.domains.news.quality import (
    GOOGLE_SHELL,
    PUBLISHER,
    TRUNCATED,
    VERDICTS,
    is_google_news_url,
    score_body,
)

ARTICLE = (
    "The United States Senate blocked a procedural vote on the CLARITY Act on "
    "Wednesday, leaving the market-structure bill without a path to the floor "
    "before the recess. Industry lobbyists had spent weeks courting centrist "
    "senators. Ripple Labs confirmed its own lobbying push in a filing.\n\n"
    "Supporters argue the bill would settle whether most tokens are securities. "
    "Critics say it hands too much discretion to the CFTC. The White House has "
    "not taken a public position on the amended text.\n\n"
    "Analysts expect the bill to return in the next session. Exchanges have "
    "already begun preparing compliance programmes in case it passes."
)
GOOGLE_URL = "https://news.google.com/rss/articles/CBMibEFVX3lxTFBqeDBXMjEw?oc=5"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (GOOGLE_URL, True),
        ("https://news.google.co.uk/rss/articles/x", True),
        ("https://coingape.com/bitcoin-etf-flows", False),
        ("https://example.com/?ref=news.google.com", False),
        ("", False),
        (None, False),
    ],
)
def test_is_google_news_url(url: str | None, expected: bool) -> None:
    assert is_google_news_url(url) is expected


def test_google_shell_is_stub_whatever_the_body() -> None:
    quality = score_body(ARTICLE, GOOGLE_URL, "Real looking title")
    assert quality.body_source == GOOGLE_SHELL
    assert quality.verdict == "stub"
    assert quality.score <= 20


def test_real_article_is_publisher_and_ok() -> None:
    quality = score_body(
        ARTICLE, "https://legis1.com/news/clarity-act", "Senate blocks CLARITY vote"
    )
    assert quality.body_source == PUBLISHER
    assert quality.verdict == "ok"
    assert 45 <= quality.score <= 100


def test_rss_fragment_is_truncated_and_never_ok() -> None:
    quality = score_body(
        "Senate blocks CLARITY Act vote, Ripple lobbies.", "https://x.com/a/1", "Short"
    )
    assert quality.body_source == TRUNCATED
    assert quality.verdict in ("stub", "thin")


def test_empty_body_is_stub() -> None:
    quality = score_body(None, "https://x.com/a/1", "Title here")
    assert quality.body_source == TRUNCATED
    assert quality.verdict == "stub"
    assert quality.score == 10  # title credit only


def test_paragraphs_raise_score_without_changing_length() -> None:
    flat = ARTICLE.replace("\n\n", " ")
    assert (
        score_body(ARTICLE, "https://x.com/a/1", "Title here").score
        >= score_body(flat, "https://x.com/a/1", "Title here").score
    )


def test_score_and_verdict_are_always_in_range() -> None:
    for size in (0, 5, 399, 400, 1999, 2000, 50_000):
        quality = score_body("word " * (size // 5), "https://x.com/a/1", "T" * 10)
        assert 0 <= quality.score <= 100
        assert quality.verdict in VERDICTS
        assert quality.body_source in (PUBLISHER, TRUNCATED, GOOGLE_SHELL)
