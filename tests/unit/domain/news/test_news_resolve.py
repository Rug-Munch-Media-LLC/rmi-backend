"""Tests for the Google News -> publisher resolver (browser seam injected)."""

from __future__ import annotations

import pytest

from app.domains.news.resolve import MAX_HOPS, Resolver

GOOGLE_URL = "https://news.google.com/rss/articles/CBMibEFVX3lxTFBqeDBXMjEw?oc=5"
PUBLISHER = "https://blog.ethereum.org/2026/10/22/glamsterdam-hoodi-delay"


def test_direct_publisher_url_passes_through_without_navigation() -> None:
    calls: list[str] = []

    def navigate(url: str, max_hops: int) -> str | None:
        calls.append(url)
        return None

    assert (
        Resolver(navigate=navigate).canonical("https://coingape.com/a/b")
        == "https://coingape.com/a/b"
    )
    assert calls == []


def test_google_url_resolves_to_publisher() -> None:
    seen: list[tuple[str, int]] = []

    def navigate(url: str, max_hops: int) -> str | None:
        seen.append((url, max_hops))
        return PUBLISHER

    assert Resolver(navigate=navigate).canonical(GOOGLE_URL) == PUBLISHER
    assert seen == [(GOOGLE_URL, MAX_HOPS)]


def test_unresolvable_google_url_returns_none() -> None:
    assert Resolver(navigate=lambda url, hops: None).canonical(GOOGLE_URL) is None


def test_navigation_that_stays_on_google_is_rejected() -> None:
    resolver = Resolver(navigate=lambda url, hops: GOOGLE_URL)
    assert resolver.canonical(GOOGLE_URL) is None


@pytest.mark.parametrize(
    "bad",
    ["", "javascript:alert(1)", "ftp://example.com/x", "/relative/path", "https://localhost/x"],
)
def test_only_absolute_http_urls_are_accepted(bad: str) -> None:
    assert Resolver(navigate=lambda url, hops: bad).canonical(GOOGLE_URL) is None


def test_hop_budget_is_configurable_and_enforced_by_the_navigator() -> None:
    seen: list[int] = []

    def navigate(url: str, max_hops: int) -> str | None:
        seen.append(max_hops)
        return PUBLISHER

    Resolver(navigate=navigate, max_hops=2).canonical(GOOGLE_URL)
    assert seen == [2]
