"""Tests for cross-platform newsletter publisher."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.newsletter.publisher import (
    ALL_PLATFORMS,
    PublishResult,
    publish_to_all,
    slugify,
)


class TestSlugify:
    """Tests for the slugify helper."""

    def test_basic_slug(self):
        assert slugify("Hello World") == "hello-world"

    def test_slug_with_special_chars(self):
        assert slugify("Crypto & DeFi News!") == "crypto-defi-news"

    def test_slug_with_unicode(self):
        assert slugify("Bitcoin 2026: The Year") == "bitcoin-2026-the-year"

    def test_empty_slug(self):
        assert slugify("") == "untitled"


class TestPublishResult:
    """Tests for PublishResult data class."""

    def test_success_result(self):
        r = PublishResult("ghost", True, platform_url="https://ghost.example.com/post/1", platform_id="abc")
        d = r.to_dict()
        assert d["platform"] == "ghost"
        assert d["success"] is True
        assert d["platform_url"] == "https://ghost.example.com/post/1"

    def test_failure_result(self):
        r = PublishResult("mirror", False, error_message="API key not configured")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error_message"] == "API key not configured"


class TestAllPlatforms:
    """Tests for platform list."""

    def test_all_platforms_defined(self):
        assert "ghost" in ALL_PLATFORMS
        assert "mirror" in ALL_PLATFORMS
        assert "paragraph" in ALL_PLATFORMS
        assert "medium" in ALL_PLATFORMS
        assert "substack" in ALL_PLATFORMS
        assert "nostr" in ALL_PLATFORMS
        assert "farcaster" in ALL_PLATFORMS
        assert "bluesky" in ALL_PLATFORMS
        assert "mastodon" in ALL_PLATFORMS
        assert "email" in ALL_PLATFORMS
        assert len(ALL_PLATFORMS) == 10


@pytest.mark.asyncio
async def test_publish_to_all_empty(monkeypatch):
    """Test publishing with no platforms (returns empty)."""
    results = await publish_to_all(
        title="Test",
        content_html="<p>Test</p>",
        platforms=[],
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_publish_to_all_unknown_platform():
    """Test publishing to an unknown platform returns error."""
    results = await publish_to_all(
        title="Test",
        content_html="<p>Test</p>",
        platforms=["unknown_platform"],
    )
    assert len(results) == 1
    assert results[0].success is False
    assert "unknown_platform" in (results[0].error_message or "")


@pytest.mark.asyncio
async def test_publish_to_ghost_missing_config():
    """Test ghost publish fails without API key."""
    results = await publish_to_all(
        title="Test",
        content_html="<p>Test</p>",
        platforms=["ghost"],
    )
    assert len(results) == 1
    assert results[0].platform == "ghost"
    assert results[0].success is False


@pytest.mark.asyncio
async def test_publish_to_mirror_missing_config():
    """Test mirror publish fails without API key."""
    results = await publish_to_all(
        title="Test",
        content_html="<p>Test</p>",
        platforms=["mirror"],
    )
    assert results[0].success is False
    assert "API key" in (results[0].error_message or "").lower()


@pytest.mark.asyncio
async def test_publish_to_paragraph_missing_config():
    """Test paragraph publish fails without API key."""
    results = await publish_to_all(
        title="Test",
        content_html="<p>Test</p>",
        platforms=["paragraph"],
    )
    assert results[0].success is False


@pytest.mark.asyncio
async def test_publish_to_farcaster_missing_config():
    """Test farcaster publish fails without API key."""
    results = await publish_to_all(
        title="Test",
        content_html="<p>Test</p>",
        platforms=["farcaster"],
    )
    assert results[0].success is False


@pytest.mark.asyncio
async def test_publish_to_bluesky_missing_config():
    """Test bluesky publish fails without credentials."""
    results = await publish_to_all(
        title="Test",
        content_html="<p>Test</p>",
        platforms=["bluesky"],
    )
    assert results[0].success is False


@pytest.mark.asyncio
async def test_publish_to_mastodon_missing_config():
    """Test mastodon publish fails without credentials."""
    results = await publish_to_all(
        title="Test",
        content_html="<p>Test</p>",
        platforms=["mastodon"],
    )
    assert results[0].success is False


@pytest.mark.asyncio
async def test_publish_to_email_always_succeeds():
    """Test email 'publish' always succeeds (delegates to service)."""
    results = await publish_to_all(
        title="Test",
        content_html="<p>Test</p>",
        platforms=["email"],
    )
    assert results[0].success is True
    assert results[0].platform_id == "email_scheduled"


@pytest.mark.asyncio
async def test_publish_multiple_platforms():
    """Test publishing to multiple platforms simultaneously."""
    results = await publish_to_all(
        title="Multi-Platform Test",
        content_html="<p>Content</p>",
        excerpt="Test excerpt",
        tags=["crypto", "news"],
        platforms=["ghost", "mirror", "nostr", "email"],
    )
    assert len(results) == 4
    # Each result should be valid even if config missing
    for r in results:
        assert r.platform in ("ghost", "mirror", "nostr", "email")


class TestPublishWithExtras:
    """Tests for platform-specific extras."""

    @pytest.mark.asyncio
    async def test_platform_extras_passed_correctly(self):
        """Test that platform extras are passed to the right publisher."""
        results = await publish_to_all(
            title="Test",
            content_html="<p>Test</p>",
            platforms=["email"],
            platform_extras={"email": {"template": "custom"}},
        )
        assert results[0].success is True
